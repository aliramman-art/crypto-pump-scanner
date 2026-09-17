name: Kraken Pattern Live Scanner

on:
  schedule:
    - cron: "*/5 * * * *"

  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: kraken-pattern-live
  cancel-in-progress: false

jobs:
  scanner:
    runs-on: ubuntu-latest
    timeout-minutes: 15

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install pandas requests numpy

      - name: Run Kraken Pattern Live Scanner
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          python kraken_pattern_live.py

      - name: Save scanner state
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

          git add kraken_pattern_live.db

          if git diff --cached --quiet; then
            echo "No database changes."
          else
            git commit -m "Update Kraken live scanner state"
            git push
          fi
