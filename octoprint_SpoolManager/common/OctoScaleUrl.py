# coding=utf-8

# Normalizing the OctoScale device address. Extracted from SpoolManagerAPI so it can be
# tested without flask/OctoPrint - the whole OctoScale HTTP layer had no tests at all, and
# this is the one piece of it that is pure.


def normalizeOctoScaleUrl(baseUrl):
    # Returns a usable base URL, or None when nothing was configured.
    if baseUrl is None:
        return None
    baseUrl = str(baseUrl).strip().rstrip("/")
    if not baseUrl:
        return None
    if not baseUrl.startswith("http://") and not baseUrl.startswith("https://"):
        # a bare "192.168.1.139" is what people type; the device serves plain HTTP
        baseUrl = "http://" + baseUrl
    return baseUrl
