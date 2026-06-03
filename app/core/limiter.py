from slowapi import Limiter
from slowapi.util import get_remote_address

# Límite global: 100 req/min para endpoints públicos.
# Los endpoints de /ml/ aplican @limiter.limit("20/minute") individualmente.
limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])
