# Billable n8n Demo Workflow

This workflow demonstrates a complete monetization cycle using **Billable** and **Telegram**:
- **Check Balance**: View virtual currency balances.
- **Trial / Bonus**: Anti-fraud "Welcome Bonus" system.
- **Microtransactions**: Buy "Stars" using Telegram Payments (XTR).
- **Exchange**: Convert "Stars" (currency) into "Predictions" (consumable item).
- **Consumption**: Spend a "Prediction" item.

## 🤖 Live Demo

Try it yourself in Telegram: **[@billable_demo_bot](https://t.me/billable_demo_bot)**

## 🚀 Setup

### 1. Backend Setup

You need a running instance of Billable.

**Simple Installation:**

If you already have a Django project:

1. Install the package:
   ```bash
   pip install billable
   ```

2. Add to `INSTALLED_APPS` and set a token in `settings.py`:
   ```python
   INSTALLED_APPS = [
       # ...
       "billable",
   ]
   
   BILLABLE_API_TOKEN = "your-secret-key"
   ```

3. Run migrations and start the server:
   ```bash
   python manage.py migrate billable
   python manage.py runserver
   ```

> **Note**: For a full production-ready template, you can check the [Billable Demo Project](https://github.com/bubinez/billable_demo).

### 2. Prerequisites (n8n Community Nodes)

This workflow uses the **Telegam Stars** community node. You must install it in your n8n instance:

1. Go to **Settings** > **Community Nodes**.
2. Click **Install**.
3. Enter package name: `n8n-nodes-telegram-stars`.
4. Install and restart n8n if needed.

### 3. Import Workflow

1. Download **[telegram_demo_workflow.json](./telegram_demo_workflow.json)**.
2. Open your n8n dashboard.
3. Select **"Import from File"** and choose the downloaded JSON.

### 4. Configuration

Open the **Config** node (the first node in the workflow) and update the values:

| Name | Description |
|------|-------------|
| `BILLABLE_URL` | Your API URL (e.g., `http://127.0.0.1:8000` or `https://api.myapp.com`). |

### 5. Credentials Setup

You need to configure **3 credentials** in n8n for this workflow to function:

#### A. Billable API (HTTP Header Auth)

1. Go to **Credentials** > **New**.
2. Search for **Header Auth**.
3. Name it `Billable API` (or similar).
4. **Name**: `Authorization`
5. **Value**: `Bearer <YOUR_BILLABLE_API_TOKEN>`
   - *Replace `<YOUR_BILLABLE_API_TOKEN>` with the token from your Django settings.*
   - **Important**: You **must** include the word `Bearer` followed by a space. The API code uses standard `HttpBearer` authentication and will reject the token without this prefix.
6. Ensure all HTTP Request nodes in the workflow use this credential.

#### B. Telegram Bot
Standard Telegram Bot API credential.

1. Search for **Telegram API**.
2. Enter your **Bot Token** (obtained from [@BotFather](https://t.me/BotFather)).

#### C. Telegram Stars (Payments)
This workflow uses the **Telegram Stars** community node for handling payments.

1. Search for **Telegram Stars API**.
2. **Setup**:
   - **Bot Token**: Use the same token as in step B.
   - **Provider Token**: Leave empty (or enter any string) if you are only using Telegram Stars (XTR). Stars are a native currency and do not require connecting an external provider like Stripe in BotFather.
