"""
userbot_own/account_management
════════════════════════════════════════════════════════════════
Account provisioning: the interactive `python add_account.py` CLI.

cli.py — interactive CLI login/relogin/edit/remove/list/verify menu
         (was add_account.py at the repo root; a thin repo-root
         add_account.py shim delegates here so `python add_account.py`
         keeps working).

History note: an in-chat add/remove-account flow (flows.py,
AccountFlowManager) previously lived in this package. It was fully
implemented but unreachable from any live command (see CHANGELOG for
version 3.0.0) and has been removed entirely as of version 3.0.1 —
see CHANGELOG for details. `python add_account.py` remains the
supported way to add or remove accounts.
════════════════════════════════════════════════════════════════
"""
