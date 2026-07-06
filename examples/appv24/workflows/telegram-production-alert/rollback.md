# Rollback

Telegram Bot API messages cannot be unsent by the appv24 production runner.

If a production alert was sent to the wrong audience, send a corrective message through a separately approved run. Do not put bot tokens, chat credentials, or raw HTTP commands in this file.
