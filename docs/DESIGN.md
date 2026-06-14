# 設計ドキュメント — healthplanet_garmin_sync

HealthPlanet（タニタ）に蓄積された体重を Garmin Connect へ非同期同期するブリッジの設計。
実装と乖離したら**このドキュメント側を実装に合わせて直す**こと（[[CLAUDE.md]] の整合ルール参照）。

## 1. 設計思想

常駐サーバではなく、**1回起動して1回同期して終了する使い捨てバッチ**。3原則で構成する。

| 原則 | 意味 | 効果 |
|---|---|---|
| ステートレスなコンテナ | コンテナ自身は状態を持たず、状態は外部(PVC)の `data/` に置く | Pod が毎回使い捨てでも継続動作 |
| 冪等（idempotent） | 同じ計測を二度上げない。空振りなら何もしない | 何度起動しても安全。CronJob と相性が良い |
| 設定の分離（12-factor） | 認証情報・挙動はすべて環境変数（k8s Secret/env） | コードに秘密を持たず public 公開可 |

スケジューリングは**アプリの外**（k8s CronJob）が担当し、アプリは「今この瞬間の差分を1回処理する」ことだけに集中する。

## 2. データフロー

```
            ┌──────────────── src/sync.py（指揮者）────────────────┐
 [state.json]→ floor算出 → 取得窓を決定                              │
            │      ▼                                                │
            │  HealthPlanetClient ──GET innerscan.json──▶ タニタAPI │
            │  (src/healthplanet.py)   体重(6021)を取得             │
            │      ▼  「last_key より新しいものだけ」を抽出          │
            │  差分フィルタ ─── 0件なら即終了（Garminに触れない）   │
            │      ▼                                                │
            │  write_weight_fit() ──▶ data/upload.fit を生成        │
            │  (src/fit_writer.py)    JST→UTC換算 + weight_scale    │
            │      ▼                                                │
            │  GarminUploader ──POST upload-service──▶ Garmin       │
            │  (src/garmin.py)   python-garminconnect でFIT送信     │
            │      ▼                                                │
            │  state.save_last_key() ── 最後に送った計測日時を記録  │
            └───────────────────────────────────────────────────────┘
```

## 3. モジュール構成（責務の分離）

| ファイル | 役割 | 外部依存 |
|---|---|---|
| `src/config.py` | 環境変数/`.env` を読み `Config` データクラスに集約 | なし |
| `src/healthplanet.py` | タニタ OAuth2（取得・更新）＋体重取得・パース | `requests` |
| `src/fit_writer.py` | 体重 → Garmin互換 FIT 生成 | `fit_tool` |
| `src/garmin.py` | Garmin ログイン＋FITアップロード | `python-garminconnect` |
| `src/state.py` | 「最後に同期した計測日時」の読み書き | 標準ライブラリ |
| `src/sync.py` | 上記を順に呼ぶオーケストレーション＋エントリポイント | 上記すべて |
| `scripts/authorize_healthplanet.py` | 初回 OAuth 認可（対話・1回だけ） | config, healthplanet |

**`sync.py` だけが全体を知り、他モジュールは互いを知らない。** 例えば Garmin 側を
`add_body_composition()` 方式へ差し替えても `garmin.py` だけの変更で済む。

## 4. 中核となる設計ポイント

### (a) 差分検出 ＝ 固定長文字列キーの大小比較
計測日時を `"YYYYMMDDHHMM"`（12桁固定長）の文字列キーとして扱う
（`healthplanet.py` の `WeightMeasurement.key`）。ゼロ詰め固定長なので日時比較が文字列比較で正しく動く。

```python
# sync.py
return [m for m in measurements if m.key > last_key]
```

`state.json` の中身は1行だけ：`{ "last_measure_key": "202606140730" }`。

### (b) 二重アップロード防止（3段構え）
手動で Garmin に入れた体重を再送しないための仕組み：

1. `state.json` … 通常運転の差分管理（最後に送った日時）
2. `SYNC_SINCE`（環境変数）… 「この日付より前は永久に対象外」の下限。
   `_effective_floor()` が `max(state, SYNC_SINCE)` を取る
3. `--seed` モード … 現在ある計測を「同期済み」として state に刻むだけで**アップロードしない**

### (c) タイムゾーンの二段変換
- HealthPlanet は **JST** で日時を返す → パース時に `tzinfo=JST` を付与（aware datetime 化）
- FIT は **UTC基準のエポックミリ秒** → `dt.timestamp()*1000` で正しい Unix 時刻へ（`fit_writer.py` の `_epoch_ms`）

aware datetime にすることで、コンテナの TZ 設定に関係なく `10:17 JST → 01:17 UTC` が常に正しく書かれる。

### (d) トークン永続化（PVC に2種類）
| トークン | 置き場所 | 取得方法 |
|---|---|---|
| HealthPlanet (access/refresh) | `data/healthplanet_tokens.json` | 初回は手動認可、以降は自動 refresh |
| Garmin セッション | `data/garmin_tokens/` | 初回 sync 実行時に自動ログインで生成（MFA無効前提） |

`python-garminconnect` の `login(tokenstore=...)` が「キャッシュ読込→期限が近ければ更新→無ければ
資格情報でログイン→保存」まで行うため、`garmin.py` は薄いラッパー。

### (e) エラーハンドリングと終了コード
`main()` が全体を `try/except` で囲み、何が起きてもスタックトレースをログ出力して `exit 1`。
CronJob 側は `backoffLimit: 2` でリトライ。アップロードの重複(409)だけは
`garmin.py` の `_is_duplicate()` で握りつぶし成功扱い。

## 5. 実行モード

エントリポイントは `python -m src.sync`（`argparse` で2モード）。

```bash
python -m src.sync          # 通常: 差分取得→FIT→アップロード→state更新
python -m src.sync --seed   # 初期化: 既存分を state に記録するだけ（アップロードなし）
```

両モードとも `run(config, seed=...)` 内で分岐し、取得処理を共有する。

## 6. ランタイム／デプロイ構造

```
[Kubernetes CronJobコントローラ]  ← スケジューラ（10:17 / 22:17 JST）
        │ Jobを生成
        ▼
   [Pod: 1コンテナ]  image: healthplanet-garmin-sync:latest
        │  uid 10001 / TZ=Asia/Tokyo
        │  ENTRYPOINT: python -m src.sync
        ├── envFrom: Secret（HP/Garmin認証情報）
        └── volumeMount: PVC → /data（トークン・state・upload.fit）
```

- **Dockerfile**: `src/` と `scripts/` だけを COPY（秘密も `data/` も焼き込まない）、非root実行
- **Secret**: 認証情報（実ファイルは `.gitignore` 済み）
- **PVC**: 唯一の永続層。トークンと state がここに残るので Pod は使い捨て可
- **CronJob**: `concurrencyPolicy: Forbid`（多重起動防止）、`backoffLimit: 2`（失敗時リトライ）

## 7. 拡張ポイント

| やりたいこと | 触る場所 |
|---|---|
| 体脂肪率等も同期 | `healthplanet.py` の取得タグ(6022等) ＋ `fit_writer.py` の `WeightScaleMessage.percent_fat` |
| FITをやめ直接API投入 | `garmin.py` を `add_body_composition()` 呼び出しに差し替え（他は無変更） |
| 実行頻度の変更 | `k8s/cronjob.yaml` の `schedule` のみ |

## まとめ

**「外部APIアダプタ（HealthPlanet/Garmin）＋ FIT変換器 ＋ 状態を持つ薄い指揮者（`sync.py`）」を、
PVCに状態を逃がした使い捨てバッチとして k8s CronJob で回す**、という素直なパイプライン設計。
