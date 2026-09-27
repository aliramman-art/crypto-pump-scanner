name: NDS M30 M1 Scanner

on:
  schedule:
    - cron: "*/15 * * * *"

  workflow_dispatch:

concurrency:
  group: nds-m30-m1-scanner
  cancel-in-progress: false

jobs:
  scanner:
    runs-on: ubuntu-latest

    timeout-minutes: 14

    steps:

      # ============================================
      # CHECKOUT
      # ============================================

      - name: Checkout repository
        uses: actions/checkout@v4

      # ============================================
      # PYTHON 3.11
      # ============================================

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      # ============================================
      # INSTALL DEPENDENCIES
      # ============================================

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install requests pandas numpy matplotlib

      # ============================================
      # RUN NDS M30 -> M1 SCANNER
      # ============================================

      - name: Run NDS M30 M1 Scanner
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          python nds_m30_m1_live_scanner.py

      # ============================================
      # DEBUG / STATUS
      # ============================================

      - name: Show scanner status
        if: always()
        run: |

          echo "=========================================="
          echo "NDS M30 -> M1 SCANNER FINISHED"
          echo "=========================================="

          echo ""
          echo "Python:"
          python --version

          echo ""
          echo "Repository files:"
          ls -lah

          echo ""
          echo "Database:"
          ls -lh nds_m30_m1_v43.db 2>/dev/null || true

          echo ""
          echo "Charts:"
          ls -lah nds_charts 2>/dev/null || true

          echo ""
          echo "=========================================="
