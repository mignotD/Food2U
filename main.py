"""Thin entrypoint so `python main.py` still works.

The implementation lives in the `food2u` package. Prefer `python -m food2u`.
"""

from food2u.app import main

if __name__ == "__main__":
    main()
