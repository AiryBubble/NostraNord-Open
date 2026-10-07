# NostraNord

## 主な機能

- 招待リンク、短縮リンク、マルウェアリンクの検出
- 日本語・英語の不適切語フィルター
- スパム、連投、フラッド、絵文字・スポイラー・Markdownスパムの対策
- 違反時のタイムアウトと、累積違反時のBAN
- サーバー認証パネルと未認証ロールの管理
- レイド対策とトークン送信防止用AutoMod
- スラッシュコマンドによる各機能・設定の管理

## url_checker.pyで使用するオススメのフィルター
- [Online Malicious URL Blocklist](https://gitlab.com/malware-filter/urlhaus-filter)
- [Phishing URL Blocklist](https://gitlab.com/malware-filter/phishing-filter)
# 注記
safetextの依存関係でPythonのバージョンは3.12.xがいいかと思われます。
