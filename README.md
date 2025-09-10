# Screener (Refactored)

**NOTE: This project has been refactored to a modular structure. The old standalone scripts are deprecated and have been removed.**

下載美股和加密貨幣的歷史數據並透過自己的策略去找到強勢標的

The purpose of this project is to download historical data for US stocks and cryptocurrencies, and use different strategies to identify strong performing assets.

## New Project Structure

The project has been refactored into a more organized, modular structure:

```
screener/
├── main.py             # New application entry point
├── config.py           # All application settings
├── .env.example        # Template for environment variables (API keys, etc.)
├── data/               # Modules for fetching and transforming data
│   ├── fetcher.py
│   └── transformer.py
├── services/           # For business logic like trading and notifications
├── strategy/           # Where different trading/screening strategies reside
└── tests/              # Unit and integration tests
```

## Getting Started

### Installation

To set up the project, follow these steps:

```bash
# 1. Clone the repository (if you haven't already)
# git clone <repository_url>
# cd screener

# 2. Install dependencies using Makefile
make install
```

### Configuration

This project uses environment variables for sensitive information like API keys and webhook URLs.

1.  **Create your environment file**: Copy the example environment file.
    ```bash
    cp .env.example .env
    ```
2.  **Edit `.env`**: Open the newly created `.env` file and replace the placeholder with your actual Discord Webhook URL.
    ```
    DISCORD_WEBHOOK_URL=your_discord_webhook_url_here
    ```
    *(Other API keys like BINANCE_API_KEY, BINANCE_API_SECRET, POLYGON_API_KEY should also be set here if needed by your strategies.)*

### Usage

This project uses a `Makefile` to simplify common operations.

```bash
# Run the application
make run

# Run all tests
make test

# Clean up generated files and caches
make clean
```

## Rollback Strategy

If a new deployment causes critical issues, a rollback can be performed using git.

1.  **Identify the problematic commit hash** using `git log`.
2.  **Revert the commit** using the command: `git revert <commit_hash>`
3.  This will create a new commit that undoes the changes from the problematic one.

## Download historical data only

To import the refactored data modules for your own usage, simply include the following lines in your Python code:

```python
from data.fetcher import StockFetcher, CryptoFetcher
from data.transformer import transform_stock_data, transform_crypto_data
```

## License

[MIT](https://choosealicense.com/licenses/mit/)
