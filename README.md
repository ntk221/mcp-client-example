# MCP Host

複数のMCP（Model Context Protocol）サーバーを管理し、Claude AIを使用して対話的なインターフェースを提供するPythonベースの実装です。

## 主な機能

- 複数サーバー対応：複数のMCPサーバーを同時に接続・管理
- 動的ツール検出：接続されたサーバーからツールを自動的に検出・公開
- 対話型インターフェース：Claude AIを使用して、利用可能な全サーバーツールにアクセス可能
- 環境変数サポート：カスタム環境変数によるサーバー設定
- Streamlitインターフェース：リアルタイムストリーミングチャットを備えたWebアプリケーション

## 必要条件

- Python 3.11以上
- uv (Pythonパッケージマネージャー)
- Claude統合用のAnthropic APIキー
- MCP互換のサーバー実装

## セットアップ

1. 環境変数の設定：
   ```bash
   # .envファイルを作成
   echo "ANTHROPIC_API_KEY=APIキー" > .env
   ```

2. 依存パッケージのインストール：
   ```bash
   uv pip install -e .
   ```

## 使い方

### コマンドラインインターフェース

以下のコマンドで1つまたは複数のMCPサーバーを起動できます：

```bash
python mcp_host.py <サーバー名> <サーバースクリプトのパス> [環境変数1=値1 ...]
```

例：
```bash
python mcp_host.py server1 mcp-server1/server.py API_KEY=xyz server2 mcp-server2/server.py TOKEN=abc
```

### Streamlitインターフェース

Webアプリケーションとして起動する場合：
```bash
streamlit run app.py -- weather ../weather/src/weather/server.py
```

ブラウザが自動的に開き、以下の機能を使用できます：
- リアルタイムストリーミングチャット
- 天気予報の取得
- 気象警報の確認

### コマンドライン引数

- `サーバー名`: サーバーの一意の識別子
- `サーバースクリプトのパス`: サーバー実装のスクリプトパス（.pyまたは.js）
- `環境変数=値`: サーバー用のオプションの環境変数

## アーキテクチャ

- `Host`: システム全体を制御するメインクラス
- `MCPConnection`: ServerとClientの接続を管理する
- `ConnectionManager`: 複数のMCPサーバー接続を管理
- `LLMManager`: Claude AIとの対話を処理
- `app.py`: Streamlitベースのウェブインターフェース
