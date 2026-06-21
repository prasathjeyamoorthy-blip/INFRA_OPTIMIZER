"""
Root conftest.py — disables the langsmith pytest plugin which crashes on
Python 3.12 due to a pydantic v1 ForwardRef incompatibility.
"""
collect_ignore_glob = []
