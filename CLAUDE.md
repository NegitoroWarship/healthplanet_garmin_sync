# CLAUDE.md — healthplanet_garmin_sync

HealthPlanet（タニタ）の体重を Garmin Connect へ同期する使い捨てバッチ。Kubernetes の CronJob で
1日2回動く。設計の詳細は [docs/DESIGN.md](docs/DESIGN.md) を参照。

## 最重要ルール：古い状態を残さない（No Stale State）

**何かを変更したら、その古い値・古い記述がリポジトリのどこにも残っていない状態にしてから完了とする。**
過去に「スケジュールを変えたのに docstring に古い `every 12 hours` が残った」事故があった。これを繰り返さない。

変更時の手順（必ず守る）:

1. **旧値を全文検索する。** 変えた値（数値・パス・名前・文言）で `grep -rn` を repo 全体にかけ、
   ヒットがゼロになるまで直す。コード・コメント・docstring・README・docs・k8s マニフェスト・
   `.env.example` すべてが対象。
2. **両方を残さない。** 「新旧併記」「コメントアウトした旧コード」を残さない。不要になったら消す。
   （設定の選択肢として意図的に残すコメントは可。その場合は理由を1行添える）
3. **ミラーを全部更新する。** 下表の「二重管理されている事実」は、1か所だけ直すと不整合になる。
   関係する全箇所を同時に直す。
4. **整合を検証する。** 下の「整合チェック」を流して、旧値が残っていないこと・新値が必要箇所に
   揃っていることを確認する。

### README は英語・日本語の2言語を必ず同期する

README は **英語 `README.md`（既定・GitHub が表示する版）** と **日本語 `README.ja.md`** の2本立て。
両ファイルの先頭に言語切替リンクを置いている。

- **片方だけ更新するのは禁止。** 手順・コマンド・スケジュール・注意書きが常に一致するよう、
  README を直すときは必ず2言語とも同じ変更を入れる。
- 以下の「二重管理されている事実」表で `README.md` と書かれた箇所は、**`README.md` と
  `README.ja.md` の両方**を指す。
- 日本語の強調は CommonMark の flanking 規則で壊れやすい。閉じ `**` が全角約物（`）」。` 等）の
  直後に来て、その次が文字だと**太字にならない**。`**1日2回**（…）` のように太字を約物の前で
  閉じるか、`**…**` の前後に半角スペースを入れて回避する。

## 二重管理されている事実（変更時は全箇所を同時更新）

| 事実 | 現在値 | 出現箇所（すべて直す） |
|---|---|---|
| 実行スケジュール | `17 10,22 * * *`（10:17 / 22:17 JST） | `k8s/cronjob.yaml`(schedule+コメント)、`README.md`(冒頭・デプロイ節・UTCフォールバック `17 1,13`)、`docs/DESIGN.md` |
| 同期する指標 | 体重のみ（タグ `6021`） | `src/healthplanet.py`(`TAG_WEIGHT`)、`src/fit_writer.py`、`README.md`(計測指標)、`docs/DESIGN.md` |
| Garmin 書込方式 | FIT生成→`upload_activity` | `src/garmin.py`、`README.md`、`docs/DESIGN.md` |
| 取得窓上限 | 90日（HealthPlanet 3ヶ月制限） | `src/sync.py`(`MAX_WINDOW_DAYS`)、`README`/`DESIGN` の「3ヶ月」記述 |
| コンテナ uid | `10001` | `Dockerfile`、`k8s/cronjob.yaml`(securityContext)、`README.md`(helper pod)、`docs/DESIGN.md` |
| イメージ名 | `healthplanet-garmin-sync:latest` | `k8s/cronjob.yaml`(image)、`README.md`(build/save)、`docs/DESIGN.md` |
| Namespace | `healthplanet-garmin-sync` | `k8s/*.yaml` 全て、`README.md` の `kubectl` コマンド |
| PVC 名 | `healthplanet-garmin-sync-data` | `k8s/pvc.yaml`、`k8s/cronjob.yaml`、`README.md`(helper pod) |
| `data/` レイアウト | `healthplanet_tokens.json` / `garmin_tokens/` / `state.json` / `upload.fit` | `src/config.py`(プロパティ)、`src/sync.py`、`README.md`、`docs/DESIGN.md` |
| README（英日2言語） | 同一内容 | `README.md`（英語）と `README.ja.md`（日本語）の**両方を必ず同時更新** |

> このアプリは設定を環境変数に寄せているので、**値そのもの**の正は基本的にコード/マニフェストの定義側。
> README（英・日）と DESIGN は説明のための**ミラー**。ズレたらミラー側を実装に合わせる。
> 表中の `README.md` は **`README.md` と `README.ja.md` の両方**を意味する。

## 整合チェック（変更後に実行）

```bash
# 旧スケジュール表記が残っていないか（例: スケジュール変更後）
grep -rnE '\*/12|every 12|12時間ごと|00:00|12:00' . --include='*.md' --include='*.yaml' --include='*.py'

# uid / イメージ名 / namespace が全箇所一致しているか
grep -rn '10001' . --include='*.yaml' --include='*'   # Dockerfile/cronjob/README/DESIGN で一致
grep -rn 'healthplanet-garmin-sync' k8s/ README.md     # namespace/PVC/image の綴り確認

# 英日READMEが両方とも同じスケジュールに言及しているか（両方ヒットすれば同期OK）
grep -l '10:17' README.md README.ja.md   # 2ファイルとも出ること

# k8s マニフェスト・埋め込みYAMLが壊れていないか
python - <<'PY'
import yaml, glob
for f in glob.glob("k8s/*.yaml"):
    list(yaml.safe_load_all(open(f)))
print("k8s YAML OK")
PY
```

## コーディング規約・前提

- **秘密情報は絶対にコミットしない。** 実認証情報は k8s Secret / `.env`（gitignore済）/ PVC のみに置く。
  `*.example` 以外の secret・トークン・`.env`・`data/` は追跡しない（`.gitignore`/`.dockerignore` で防御済）。
- **モジュールは責務を越境しない。** `healthplanet.py` は Garmin を知らない／`fit_writer.py` は HealthPlanet を
  知らない。横断ロジックは `sync.py` に集約する。
- **冪等性を壊さない。** 同じ計測を二度上げない設計（`state` ＋ `SYNC_SINCE` ＋ `--seed`）。新機能でこの
  不変条件を破らない。
- **時刻は aware datetime で扱う。** HealthPlanet=JST を付与してから FIT=UTCエポックms へ変換。naive
  datetime を持ち回らない。
- **依存追加は最小限。** 現状 `garminconnect` / `fit-tool` / `requests` のみ。増やす場合は
  `requirements.txt` と `Dockerfile`・`docs/DESIGN.md` の依存記述も合わせる。

## 変更影響の早見表

| 変えるもの | 連動して直す場所 |
|---|---|
| スケジュール | 上表「実行スケジュール」の全箇所（docstring は汎用表現のままでよい） |
| 同期指標を追加 | `healthplanet.py` 取得タグ＋`fit_writer.py`＋README計測指標＋DESIGN拡張ポイント |
| `data/` の構成変更 | `config.py` プロパティ＋README／DESIGN／（必要なら k8s helper pod の cp 手順） |
| uid / イメージ名 / namespace / PVC名 | 上表の対応行すべて |
| 依存ライブラリ | `requirements.txt`＋`Dockerfile`＋DESIGN のモジュール表 |
| README を編集 | `README.md`（英）と `README.ja.md`（日）を**必ず両方同期** |
