# healthplanet_garmin_sync

📖 [English README → README.md](README.md) · **日本語**

タニタの **HealthPlanet** に蓄積された体重を、**Garmin Connect** へ非同期で同期するブリッジ。
homelab の Kubernetes 上で **1日2回** （10:17 / 22:17 JST）動き、**新しい計測があった時だけ** Garmin にアップロードする。

```
HealthPlanet API (体重 tag 6021)
      │  OAuth2 + innerscan.json
      ▼
  差分抽出 (state.json: 最後に送った計測日時より新しいものだけ)
      │
      ▼
  FIT 生成 (fit_tool: weight_scale_message)
      │
      ▼
  Garmin Connect (python-garminconnect: upload-service へ FIT を POST)
```

新規がゼロ件なら Garmin への接続もせず即終了する。

> 設計の詳細は [docs/DESIGN.md](docs/DESIGN.md)。リポジトリの変更規約（古い記述を残さない等）は
> [CLAUDE.md](CLAUDE.md) を参照。

## 構成

| ファイル | 役割 |
|---|---|
| `src/healthplanet.py` | OAuth2 トークン更新 + innerscan から体重取得 |
| `src/fit_writer.py`   | 体重 → FIT 変換（`fit_tool`） |
| `src/garmin.py`       | Garmin ログイン + FIT アップロード（`python-garminconnect`） |
| `src/state.py`        | 最後に同期した計測日時の保存／読み出し |
| `src/sync.py`         | 全体のオーケストレーション（エントリポイント） |
| `scripts/authorize_healthplanet.py` | 初回 OAuth 認可（手動・1回だけ） |
| `k8s/`                | Namespace / PVC / Secret / CronJob |

トークン（HealthPlanet・Garmin）と state は `DATA_DIR`（既定 `./data`、k8s では PVC `/data`）に永続化される。

## 認証情報

- **HealthPlanet**: <https://www.healthplanet.jp/apis/registinfo.do> でアプリ登録し
  `client_id` / `client_secret` を取得。`redirect_uri` は登録した URL に合わせる
  （非Webアプリは `https://www.healthplanet.jp/success.html` が使える）。
- **Garmin Connect**: メール / パスワード（MFA 無効前提）。

> 注: Garmin への書き込みは公式公開 API ではなく `upload-service` 経由（Garmin アプリと同じ
> import 経路）。Garmin 側の仕様変更で動かなくなる可能性がある個人利用向けの方式。

## ローカルでの実行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 値を埋める

# 1) HealthPlanet を初回認可（access/refresh トークンを data/ に保存）
python -m scripts.authorize_healthplanet

# 2) 【重要】既存の体重を再アップロードしないよう初期化（後述）
python -m src.sync --seed

# 3) 同期を実行（--seed 以降に計測した新規のみアップロード）
python -m src.sync
```

### 既存の体重を二重登録しないために（初回だけ必須）

Garmin に手動入力済みの体重がある場合、初回同期で HealthPlanet の履歴を一括アップロードすると
**二重登録** になる。これを防ぐため、初回認可の直後に **一度だけ** seed を実行する:

```bash
python -m src.sync --seed
```

`--seed` は「現在 HealthPlanet にある計測まではすべて同期済み」と state に記録するだけで、
**Garmin へは何もアップロードしない** 。以降の `python -m src.sync` は **seed 実行後に新しく計測した体重だけ** を送る。

代わりに環境変数 `SYNC_SINCE=20260614`（その日付より後の計測だけ対象）でも同じ目的を達成できる。
k8s では Secret/環境変数で `SYNC_SINCE` を入れておくのが手軽（seed の手動実行が不要になる）。

初回の Garmin ログインで `data/garmin_tokens/` にセッションが保存され、以降は再ログイン不要
（トークンは約1年有効・自動更新）。

## Docker

```bash
docker build -t healthplanet-garmin-sync:latest .
docker run --rm -v "$PWD/data:/data" --env-file .env healthplanet-garmin-sync:latest
```

## Kubernetes へのデプロイ

`k8s/` に Namespace / PVC / Secret(雛形) / CronJob を同梱。homelab の Kubernetes で1日2回（10:17 / 22:17 JST）動かす。

ポイント:

- 事前に PVC へ入れる必要があるのは **HealthPlanet のトークンだけ** 。
  Garmin は MFA 無効なら **初回 CronJob 実行時に Secret の資格情報で自動ログイン** し、
  セッションを PVC(`/data/garmin_tokens/`) に保存するため事前投入は不要。
- 既存の体重を二重登録しないため、 **初回ロールアウト時に `SYNC_SINCE` を設定** するのが最も手軽
  （`k8s/cronjob.yaml` のコメントを外して日付を入れる）。

### 0. 前提

- `kubectl` が homelab の Kubernetes クラスタを指していること（`kubectl get nodes` で確認）。
- イメージを **Job を実行するノードに載せる** こと（下記）。複数ノードなら全ノードに取り込むか
  プライベートレジストリを使う。

### 1. イメージをビルドしてクラスタのノードに載せる

```bash
docker build -t healthplanet-garmin-sync:latest .
docker save healthplanet-garmin-sync:latest -o /tmp/hpgs.tar
# ノードのコンテナランタイムへ side-load する。コマンドはランタイム依存で、
# k3s(containerd) の場合は次のとおり:
sudo k3s ctr images import /tmp/hpgs.tar          # 各ノードで実行
```

> もしくはクラスタが pull できるコンテナレジストリへ push し、`k8s/cronjob.yaml` の
> `image:` をそのタグに合わせる（複数ノードでの標準的なやり方）。

### 2. Namespace / PVC / Secret を作成

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/pvc.yaml

# Secret 雛形をコピーして実値を記入（secret.yaml は .gitignore 済み＝コミットされない）
cp k8s/secret.example.yaml k8s/secret.yaml
# 値を埋める: HEALTHPLANET_CLIENT_ID/SECRET, GARMIN_EMAIL/PASSWORD など
kubectl apply -f k8s/secret.yaml
```

### 3. HealthPlanet トークンを PVC へ投入（初回だけ）

OAuth 認可はブラウザが要るためローカルで実施し、得たトークン **ファイル1つ** を PVC へコピーする。

```bash
# (a) ローカルで初回認可（data/healthplanet_tokens.json が生成される）
python -m scripts.authorize_healthplanet

# (b) PVC をマウントする使い捨て Pod を立てる（appuser と同じ uid:10001 で動かす）
kubectl -n healthplanet-garmin-sync apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: hpgs-seed
spec:
  restartPolicy: Never
  securityContext:
    runAsUser: 10001
    runAsGroup: 10001
    fsGroup: 10001
  containers:
    - name: shell
      image: busybox:1.36
      command: ["sh", "-c", "sleep 3600"]
      volumeMounts:
        - { name: data, mountPath: /data }
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: healthplanet-garmin-sync-data
EOF
kubectl -n healthplanet-garmin-sync wait --for=condition=Ready pod/hpgs-seed --timeout=60s

# (c) HealthPlanet トークンだけコピー
kubectl -n healthplanet-garmin-sync cp ./data/healthplanet_tokens.json hpgs-seed:/data/healthplanet_tokens.json

# (任意) ローカルで `python -m src.sync --seed` 済みなら state も入れれば SYNC_SINCE 不要
# kubectl -n healthplanet-garmin-sync cp ./data/state.json hpgs-seed:/data/state.json

# (d) 後始末
kubectl -n healthplanet-garmin-sync delete pod hpgs-seed
```

> `state.json` を入れない場合は、手順4の前に `k8s/cronjob.yaml` の `SYNC_SINCE` を有効化して
> おくこと（さもないと初回実行で過去分が一括アップロードされる）。

### 4. CronJob をデプロイ

```bash
kubectl apply -f k8s/cronjob.yaml
kubectl -n healthplanet-garmin-sync get cronjob   # SCHEDULE/TIMEZONE を確認
```

### 5. 手動トリガで疎通確認

```bash
kubectl -n healthplanet-garmin-sync create job --from=cronjob/healthplanet-garmin-sync manual-1
kubectl -n healthplanet-garmin-sync logs -f job/manual-1
```

ログに `Garmin login OK` と `No new measurements`（または `uploaded N measurement(s)`）が出れば成功。
この初回実行で `/data/garmin_tokens/` も自動生成され、以降は再ログイン不要。

### 6. 運用

```bash
# スケジュール実行の履歴とログ
kubectl -n healthplanet-garmin-sync get jobs
kubectl -n healthplanet-garmin-sync logs job/<job-name>

# 一時停止 / 再開
kubectl -n healthplanet-garmin-sync patch cronjob healthplanet-garmin-sync -p '{"spec":{"suspend":true}}'
kubectl -n healthplanet-garmin-sync patch cronjob healthplanet-garmin-sync -p '{"spec":{"suspend":false}}'

# コード更新時: 再ビルド → ノードに再 import → 新しいタグで cronjob を更新
#   （:latest を使い回す場合も import し直せば次回 Job から反映される）
```

> `schedule`/`timeZone` を変えたいときは `k8s/cronjob.yaml` を編集して `kubectl apply` し直す。
> 分を `:00` ちょうどにせず半端な分にしてあるのは、毎時0分に集中するアクセスを避けるため。
> `timeZone` は Kubernetes **v1.27 以降** が必要。古い場合はこの行を消し、UTC基準で
> `schedule: "17 1,13 * * *"`（= 10:17 / 22:17 JST）に書き換える。

## トラブルシュート

- **`No HealthPlanet tokens found`**: `scripts/authorize_healthplanet.py` を未実行。
- **HealthPlanet refresh 失敗 / 401**: refresh_token 失効。再度認可スクリプトを実行。
- **Garmin 認証失敗**: パスワード誤り / アカウントロック / MFA が有効になっている。
  MFA を有効にした場合は `src/garmin.py` で `prompt_mfa` を渡す対応が必要。
- **日付がずれる**: コンテナ `TZ=Asia/Tokyo` を確認（FIT は UTC 換算で書く）。

## 計測指標

現状は **体重のみ** （HealthPlanet タグ 6021）。体脂肪率(6022)等を追加したい場合は
`healthplanet.py` の取得タグと `fit_writer.py` の `WeightScaleMessage.percent_fat` 等を拡張する。
