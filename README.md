# Discord鍛冶屋Bot

Replitで作成したPython製Discord Botです。Discord Gatewayへの接続のみを行うため、Web公開用ポートは不要です。

## 必要な環境変数

本番環境では値をGitHubに置かず、ConoHa VPS上の /etc/weapon-bot.env に設定してください。

| 変数 | 必須 | 説明 |
|---|---|---|
| DISCORD_BOT_TOKEN | はい | Discord Botトークン |
| NAGI_API_BASE_URL | はい | 凪APIのHTTPSベースURL |
| NAGI_API_TOKEN | はい | 凪API認証トークン |
| CREW_ROLE_NAME | いいえ | 対象ロール名（既定値: 本船員） |
| WEAPON_DB_PATH | いいえ | SQLite保存先（既定値: data/pirate.db） |
| LOG_LEVEL | いいえ | ログレベル（既定値: INFO） |

## ConoHa VPS (Ubuntu 24.04) への配置

1. VPSでGitとPython仮想環境を準備します。

   
   sudo apt update
   sudo apt install -y git python3 python3-venv
   sudo useradd --system --create-home --home-dir /var/lib/weapon-bot --shell /usr/sbin/nologin weapon-bot
   sudo install -d -o weapon-bot -g weapon-bot /opt/nagi-blacksmith
   

2. GitHubのリポジトリ設定から、VPSで作ったSSH公開鍵を読み取り専用Deploy keyとして登録し、Botユーザーでクローンします。秘密鍵はVPS内だけに保管し、GitHubやチャットへ貼らないでください。

   
   sudo -u weapon-bot -H install -d -m 700 /var/lib/weapon-bot/.ssh
   sudo -u weapon-bot -H ssh-keygen -t ed25519 -C "nagi-blacksmith-conoha" -f /var/lib/weapon-bot/.ssh/id_ed25519 -N ""
   sudo cat /var/lib/weapon-bot/.ssh/id_ed25519.pub
   sudo -u weapon-bot -H git clone git@github.com:sakura36912/nagi-blacksmith.git /opt/nagi-blacksmith
   

3. 依存関係を入れ、秘密の環境変数ファイルを作ります。

   
   sudo -u weapon-bot -H python3 -m venv /opt/nagi-blacksmith/.venv
   sudo -u weapon-bot -H /opt/nagi-blacksmith/.venv/bin/pip install -r /opt/nagi-blacksmith/requirements.txt
   sudo install -o root -g root -m 600 /dev/null /etc/weapon-bot.env
   sudo nano /etc/weapon-bot.env
   

   /etc/weapon-bot.env に DISCORD_BOT_TOKEN、NAGI_API_BASE_URL、NAGI_API_TOKEN を設定します。任意で CREW_ROLE_NAME と WEAPON_DB_PATH=/opt/nagi-blacksmith/data/pirate.db も設定できます。

4. systemdサービスを登録して自動起動します。

   
   sudo install -m 644 /opt/nagi-blacksmith/deploy/weapon-bot.service /etc/systemd/system/weapon-bot.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now weapon-bot
   sudo systemctl status weapon-bot
   sudo journalctl -u weapon-bot -f
   

更新時はBotを止めてから取得し直します: sudo systemctl stop weapon-bot → sudo -u weapon-bot -H git -C /opt/nagi-blacksmith pull --ff-only → sudo systemctl start weapon-bot。

## データと切り替え時の注意

- .env、SQLiteデータベース、トークンはGitHubにコミットしません。
- 残高と武器の正本は凪APIです。ローカルSQLiteにはボスイベントや戦闘状態などが保存されます。
- Replit側の data/pirate.db はGitHubには含まれません。必要な状態を引き継ぐ場合は、Replit側のBotを停止してDBをバックアップし、安全な経路でVPSへ別途コピーしてください。
- 同じDiscord Botトークンを使うBotは、切り替え時にReplitとVPSで同時起動しないでください。
- BotはDiscordへの外向き接続を使うため、ConoHa側でWeb用の受信ポートを開ける必要はありません。
