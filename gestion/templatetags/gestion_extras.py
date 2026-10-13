from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def gnf(valeur):
    """Formate un montant : 1 250 000 GNF."""
    try:
        n = int(Decimal(valeur or 0))
    except (TypeError, ValueError, ArithmeticError):
        return "—"
    return f"{n:,}".replace(",", "\u202f") + " GNF"


@register.filter
def nombre(valeur):
    try:
        return f"{int(valeur):,}".replace(",", "\u202f")
    except (TypeError, ValueError):
        return "—"
