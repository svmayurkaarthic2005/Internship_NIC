"""
Master Test Runner for SIS Question Mark Regression Suite.
Runs:
1. test_qmark_classification.py (Unit / deterministic parsing)
2. test_qmark_multiturn_integration.py (Multi-turn integration)
"""
import subprocess
import sys

def main():
    print("=" * 60)
    print("RUNNING PART 1: Question Mark Classification Unit Tests")
    print("=" * 60)
    res1 = subprocess.run([sys.executable, "SIS_Question_Mark_Regression_Tests/test_qmark_classification.py"])

    print("\n" + "=" * 60)
    print("RUNNING PART 2: Multi-Turn Question Mark Integration Suite")
    print("=" * 60)
    res2 = subprocess.run([sys.executable, "SIS_Question_Mark_Regression_Tests/test_qmark_multiturn_integration.py"])

    if res1.returncode == 0 and res2.returncode == 0:
        print("\nAll Question Mark Regression Test Suites completed successfully.")
    else:
        print("\nSome tests reported failures (see failure cases and audit report).")

if __name__ == "__main__":
    main()
