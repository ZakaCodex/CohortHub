"""بدائل خفيفة لحزم غير متوفرة محليًا (telegram, httpx) كي نختبر منطق البوت.
تُستخدم فقط إذا لم تكن الحزمة الحقيقية مثبّتة."""
import importlib.util
import sys
import types


def _mod(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


def install():
    if "httpx" not in sys.modules and importlib.util.find_spec("httpx") is None:
        _mod("httpx")
    if "telegram" in sys.modules or importlib.util.find_spec("telegram") is not None:
        return

    class TelegramError(Exception):
        def __init__(self, message=""):
            super().__init__(message)
            self.message = message

    class NetworkError(TelegramError):
        pass

    class BadRequest(NetworkError):
        pass

    class TimedOut(NetworkError):
        pass

    class Forbidden(TelegramError):
        pass

    class RetryAfter(TelegramError):
        def __init__(self, retry_after=1):
            super().__init__("flood")
            self.retry_after = retry_after

    class Simple:
        def __init__(self, *a, **kw):
            self.args, self.kw = a, kw

    class Poll(Simple):
        QUIZ = "quiz"
        REGULAR = "regular"

    class Markup(Simple):
        def __init__(self, rows=None, **kw):
            super().__init__(**kw)
            self.rows = rows

    _mod("telegram", InlineKeyboardButton=Simple, InlineKeyboardMarkup=Markup, WebAppInfo=Simple,
         MenuButtonWebApp=Simple, Update=Simple, Poll=Poll)
    _mod("telegram.error", TelegramError=TelegramError, NetworkError=TelegramError, BadRequest=BadRequest,
         TimedOut=TimedOut, Forbidden=Forbidden, RetryAfter=RetryAfter)
    _mod("telegram.ext", Application=Simple, CallbackQueryHandler=Simple, CommandHandler=Simple,
         MessageHandler=Simple, PollAnswerHandler=Simple,
         ContextTypes=types.SimpleNamespace(DEFAULT_TYPE=object), filters=Simple())
