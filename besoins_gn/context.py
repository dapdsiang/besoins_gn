from django.conf import settings


def globales(request):
    ctx = {
        "APP_TITRE": settings.APP_TITRE, "APP_INSTITUTION": settings.APP_INSTITUTION,
        "APP_SIGLE": settings.APP_SIGLE, "APP_DEVISE": settings.APP_DEVISE,
        "APP_LOGO": settings.APP_LOGO,
        "nb_a_valider": 0, "nb_a_arbitrer": 0, "nb_a_servir": 0,
    }
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        from gestion.models import Demande
        qs = Demande.objects
        ctx["nb_a_valider"] = Demande.a_valider_par(user).count()
        if user.peut_arbitrer:
            ctx["nb_a_arbitrer"] = qs.filter(statut=Demande.Statut.VALIDEE).count()
        if user.peut_gerer_stock:
            ctx["nb_a_servir"] = qs.filter(
                statut__in=[Demande.Statut.APPROUVEE, Demande.Statut.SERVIE_PARTIEL]).count()
    return ctx
