#!/usr/bin/env python3
"""
Integration check script to verify the bot structure and basic syntax.
This doesn't actually run the bot (which would require API keys),
but verifies that all modules can be parsed and have the expected structure.
"""

import ast
import sys
import os

def check_syntax(file_path):
    """Check if a Python file has valid syntax."""
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        ast.parse(content)
        return True, None
    except SyntaxError as e:
        return False, f"Syntax error: {e}"
    except Exception as e:
        return False, f"Error reading file: {e}"

def check_file_exists(file_path):
    """Check if a file exists."""
    return os.path.isfile(file_path)

def main():
    """Main integration check."""
    print("Running integration check for BTC Polymarket Bot...")
    print("=" * 50)

    # List of required files from CLAUDE.md
    required_files = [
        'config.py',
        'db.py',
        'requirements.txt',
        '.env.example',
        'price_feed.py',
        'predictor.py',
        'risk_manager.py',
        'market_finder.py',
        'order_manager.py',
        'bot.py',
        'telegram_alerts.py',
        'train_model.py',
        'backtest.py',
        'README.md'
    ]

    all_good = True

    # Check file existence
    print("Checking file existence:")
    for file_name in required_files:
        file_path = os.path.join('btc_bot', file_name)
        if check_file_exists(file_path):
            print(f"  ✓ {file_name}")
        else:
            print(f"  ✗ {file_name} - MISSING")
            all_good = False

    print("\nChecking Python syntax:")
    # Check syntax of Python files
    python_files = [f for f in required_files if f.endswith('.py')]
    for file_name in python_files:
        file_path = os.path.join('btc_bot', file_name)
        if check_file_exists(file_path):
            success, error = check_syntax(file_path)
            if success:
                print(f"  ✓ {file_name}")
            else:
                print(f"  ✗ {file_name} - {error}")
                all_good = False
        else:
            print(f"  ✗ {file_name} - MISSING (skipping syntax check)")
            all_good = False

    print("\n" + "=" * 50)
    if all_good:
        print("✓ All integration checks passed!")
        print("\nNext steps:")
        print("1. Fill in your API keys in .env")
        print("2. Install dependencies: pip install -r requirements.txt")
        print("3. Train the model: python train_model.py")
        print("4. Run backtest: python backtest.py --start-date 2024-01-01 --end-date 2024-12-31")
        print("5. Start paper trading: python bot.py")
    else:
        print("✗ Some integration checks failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()