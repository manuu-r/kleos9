"""DigiKey Product Information V4: bounded exact-SKU GET lookups only."""
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from pydantic import SecretStr

from kleos.capabilities.external import ReadOnlyHTTP, ReadOnlyProvider
from kleos.contracts.models import DomainError


def price(value):
    try:
        encoded = str(value)
        if len(encoded) > 128:
            raise ValueError()
        result = Decimal(encoded)
        if not result.is_finite() or result < 0 or result > 100000000:
            raise ValueError()
        digits = result.as_tuple()
        if not -12 <= digits.exponent <= 8 or len(digits.digits) > 24:
            raise ValueError()
        return format(result, "f")
    except (ValueError, InvalidOperation):
        raise DomainError("Malformed catalog pricing", "provider_response", 502) from None


def integer(value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DomainError("Malformed catalog quantity", "provider_response", 502)
    return value


class DigiKeyCatalog(ReadOnlyProvider):
    id, version, kind = "digikey", "product-information-v4", "sourcing"

    def __init__(self, *, client_id: SecretStr, access_token: SecretStr,
                 account_id: SecretStr | None = None, transport=None):
        if not client_id.get_secret_value() or not access_token.get_secret_value():
            raise ValueError("DigiKey credentials must be injected by server configuration")
        headers = {
            "X-DIGIKEY-Client-Id": client_id.get_secret_value(),
            "Authorization": f"Bearer {access_token.get_secret_value()}",
            "X-DIGIKEY-Locale-Site": "US", "X-DIGIKEY-Locale-Language": "en",
            "X-DIGIKEY-Locale-Currency": "USD"}
        if account_id and account_id.get_secret_value():
            headers["X-DIGIKEY-Account-Id"] = account_id.get_secret_value()
        self.http = ReadOnlyHTTP("https://api.digikey.com/products/v4/", headers, transport=transport)

    async def discover(self, request):
        return await self.quote(request)

    async def quote(self, request):
        if request.currency != "USD":
            raise DomainError("This catalog installation is configured for USD", "currency_unavailable", 422)
        if len(request.bom) > 20:
            raise DomainError("Split catalog lookups into at most 20 BOM items", "lookup_limit", 422)
        items = []
        for item in request.bom:
            if item.supplier_part in {".", ".."}:
                raise DomainError("Invalid supplier SKU", "invalid_request", 422)
            raw = await self.http.get(f"search/{quote(item.supplier_part, safe='')}/productdetails")
            product = raw.get("Product")
            locale = raw.get("SearchLocaleUsed", {})
            if not isinstance(product, dict) or not isinstance(locale, dict) or locale.get("Currency") != "USD":
                raise DomainError("Catalog locale or product is missing", "provider_response", 502)
            variants = product.get("ProductVariations", [])
            if not isinstance(variants, list) or len(variants) > 100:
                raise DomainError("Malformed catalog variants", "provider_response", 502)
            matches = [v for v in variants if isinstance(v, dict)
                       and v.get("DigiKeyProductNumber") == item.supplier_part]
            if len(matches) != 1:
                items.append({"reference": item.reference, "supplierPart": item.supplier_part,
                              "status": "UNKNOWN", "reason": "Exact package SKU required"})
                continue
            variant = matches[0]
            breaks = variant.get("StandardPricing", [])
            if not isinstance(breaks, list) or len(breaks) > 100:
                raise DomainError("Malformed catalog price breaks", "provider_response", 502)
            eligible = []
            for tier in breaks:
                if not isinstance(tier, dict):
                    raise DomainError("Malformed catalog price break", "provider_response", 502)
                minimum = integer(tier.get("BreakQuantity"))
                unit_price = price(tier.get("UnitPrice"))
                if minimum <= item.quantity:
                    eligible.append((minimum, unit_price))
            unit_price = max(eligible, key=lambda pair: pair[0])[1] if eligible else None
            available = integer(variant.get("QuantityAvailableforPackageType"))
            moq = integer(variant.get("MinimumOrderQuantity"))
            items.append({"reference": item.reference, "supplierPart": item.supplier_part,
                "quantity": item.quantity, "available": available, "minimumOrderQuantity": moq,
                "unitPrice": unit_price, "currency": "USD", "status": "OBSERVED",
                "quantityAvailable": available >= item.quantity and item.quantity >= moq,
                "manufacturerPart": str(product.get("ManufacturerProductNumber", ""))[:200]})
        return {"items": items, "bindingQuote": False, "reserved": False,
                "includesShippingAndTax": False, "physicalAcceptance": False}

    async def close(self):
        await self.http.close()
