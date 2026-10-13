import csv
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from comptes.models import Structure

from . import forms as f
from .models import (Article, Affectation, BonSortie, Commande, Demande, EnveloppeBudgetaire, Equipement,
                     Fournisseur, HistoriqueDemande, LigneBonSortie, Livraison, LigneLivraison,
                     LigneDemande, MouvementStock, annee_courante)
from .services import StockInsuffisant, mouvement, plan_achats

S = Demande.Statut
T = MouvementStock.Type


def role_requis(propriete):
    def deco(view):
        @login_required
        @wraps(view)
        def wrapper(request, *a, **kw):
            if not getattr(request.user, propriete):
                raise PermissionDenied
            return view(request, *a, **kw)
        return wrapper
    return deco


def _historiser(demande, user, action, commentaire=""):
    HistoriqueDemande.objects.create(demande=demande, utilisateur=user, action=action, commentaire=commentaire)


def _demande_visible(request, pk):
    return get_object_or_404(Demande.objects.visibles_pour(request.user), pk=pk)


def _csv(nom, entetes, lignes):
    rep = HttpResponse(content_type="text/csv; charset=utf-8")
    rep["Content-Disposition"] = f'attachment; filename="{nom}"'
    rep.write("\ufeff")
    w = csv.writer(rep, delimiter=";")
    w.writerow(entetes)
    w.writerows(lignes)
    return rep


def _page(request, qs, n=25):
    params = request.GET.copy()
    params.pop("page", None)
    return Paginator(qs, n).get_page(request.GET.get("page")), params.urlencode()


# ================================================================ Tableau de bord
@login_required
def accueil(request):
    u, exercice = request.user, annee_courante()
    visibles = Demande.objects.visibles_pour(u)
    ctx = {
        "exercice": exercice,
        "en_cours": visibles.filter(statut__in=[S.SOUMISE, S.VALIDEE, S.APPROUVEE, S.SERVIE_PARTIEL]).count(),
        "mes_brouillons": visibles.filter(statut=S.BROUILLON, demandeur=u).count(),
        "a_valider": Demande.a_valider_par(u)[:6],
        "recentes": visibles.select_related("structure", "demandeur")[:8],
    }
    if u.peut_arbitrer:
        ctx["a_arbitrer"] = Demande.objects.filter(statut=S.VALIDEE).select_related("structure")[:6]
    if u.peut_gerer_stock:
        ctx["a_servir"] = Demande.objects.filter(
            statut__in=[S.APPROUVEE, S.SERVIE_PARTIEL]).select_related("structure")[:6]
    if u.vue_globale:
        alertes = [a for a in Article.objects.filter(actif=True) if a.niveau[0] != "OK"]
        ctx["alertes"] = alertes[:8]
        ctx["nb_alertes"] = len(alertes)
        ctx["commandes_en_cours"] = Commande.objects.filter(
            statut__in=[Commande.Statut.ENVOYEE, Commande.Statut.PARTIELLE]).count()
        ctx["equipements_reparation"] = Equipement.objects.filter(statut=Equipement.Statut.EN_REPARATION).count()
        alloue = EnveloppeBudgetaire.objects.filter(exercice=exercice).aggregate(t=Sum("montant_alloue"))["t"] or 0
        engage = Demande.engage(exercice)
        ctx["budget"] = {"alloue": alloue, "engage": engage, "libelle": "Ensemble des directions",
                         "taux": int(engage * 100 / alloue) if alloue else 0}
    elif u.structure_id:
        env = EnveloppeBudgetaire.objects.filter(exercice=exercice, structure=u.structure).first()
        if env:
            ctx["budget"] = {"alloue": env.montant_alloue, "engage": env.engage, "libelle": str(u.structure),
                             "taux": env.taux_engage}
    return render(request, "gestion/accueil.html", ctx)


# ================================================================ Demandes
@login_required
def demandes(request):
    qs = Demande.objects.visibles_pour(request.user).select_related("structure", "demandeur")
    form = f.FiltreDemandeForm(request.GET or None)
    if form.is_valid():
        d = form.cleaned_data
        if d["q"]:
            qs = qs.filter(Q(reference__icontains=d["q"]) | Q(objet__icontains=d["q"]))
        for champ in ("statut", "priorite", "structure", "exercice"):
            if d[champ]:
                qs = qs.filter(**{champ: d[champ]})
        if d["du"]:
            qs = qs.filter(cree_le__date__gte=d["du"])
        if d["au"]:
            qs = qs.filter(cree_le__date__lte=d["au"])
    page, params = _page(request, qs)
    return render(request, "gestion/demandes.html", {"page": page, "form": form, "params": params})


def _enregistrer_demande(request, demande, form, formset):
    with transaction.atomic():
        d = form.save(commit=False)
        if not d.pk:
            d.demandeur = request.user
        d.save()
        formset.instance = d
        formset.save()
    return d


@role_requis("peut_demander")
def demande_creer(request):
    FS = f.formset_demande(extra=4)
    form = f.DemandeForm(request.POST or None, user=request.user)
    formset = FS(request.POST or None, instance=Demande())
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        d = _enregistrer_demande(request, Demande(), form, formset)
        _historiser(d, request.user, "Création (brouillon)")
        messages.success(request, f"Demande {d.reference} enregistrée en brouillon. Soumettez-la quand elle est prête.")
        return redirect("demande_detail", pk=d.pk)
    return render(request, "gestion/demande_form.html",
                  {"form": form, "formset": formset, "titre_page": "Nouvelle expression de besoins"})


@login_required
def demande_modifier(request, pk):
    d = _demande_visible(request, pk)
    if d.statut != S.BROUILLON or not (d.demandeur_id == request.user.id or request.user.est_admin):
        raise PermissionDenied
    FS = f.formset_demande(extra=2)
    form = f.DemandeForm(request.POST or None, instance=d, user=request.user)
    formset = FS(request.POST or None, instance=d)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        _enregistrer_demande(request, d, form, formset)
        _historiser(d, request.user, "Modification du brouillon")
        messages.success(request, "Demande mise à jour.")
        return redirect("demande_detail", pk=d.pk)
    return render(request, "gestion/demande_form.html",
                  {"form": form, "formset": formset, "titre_page": f"Modifier {d.reference}"})


def _budget_info(d, montant=None):
    env = EnveloppeBudgetaire.objects.filter(exercice=d.exercice, structure=d.structure).first()
    alloue = env.montant_alloue if env else Decimal(0)
    engage = Demande.engage(d.exercice, d.structure, exclure=d)
    return {"enveloppe": env, "alloue": alloue, "engage": engage, "disponible": alloue - engage,
            "montant_demande": d.montant_demande}


@login_required
def demande_detail(request, pk):
    d = _demande_visible(request, pk)
    u = request.user
    modifiable = d.statut == S.BROUILLON and (d.demandeur_id == u.id or u.est_admin)
    ctx = {
        "d": d, "lignes": d.lignes.select_related("article"),
        "historique": d.historique.select_related("utilisateur"),
        "bons": d.bons_sortie.all(),
        "modifiable": modifiable,
        "peut_soumettre": modifiable and d.lignes.exists(),
        "peut_valider": d.peut_etre_valide_par(u),
        "peut_arbitrer": d.statut == S.VALIDEE and u.peut_arbitrer,
        "peut_servir": d.statut in (S.APPROUVEE, S.SERVIE_PARTIEL) and u.peut_gerer_stock,
        "peut_cloturer": d.statut == S.SERVIE_PARTIEL and (u.peut_gerer_stock or u.peut_arbitrer),
        "peut_annuler": d.statut in (S.BROUILLON, S.SOUMISE, S.VALIDEE) and (d.demandeur_id == u.id or u.peut_arbitrer),
        "budget": _budget_info(d) if (u.vue_globale or u.role == u.Role.RESPONSABLE) else None,
        "etapes": _etapes(d),
    }
    return render(request, "gestion/demande_detail.html", ctx)


def _etapes(d):
    ordre = [("Brouillon", S.BROUILLON), ("Validation hiérarchique", S.SOUMISE), ("Arbitrage budgétaire", S.VALIDEE),
             ("À servir", S.APPROUVEE), ("Servie", S.SERVIE)]
    rang = {S.BROUILLON: 0, S.SOUMISE: 1, S.VALIDEE: 2, S.APPROUVEE: 3, S.SERVIE_PARTIEL: 3, S.SERVIE: 5}
    if d.statut in (S.REJETEE, S.ANNULEE):
        return []
    courant = rang[d.statut]
    return [(lib, "fait" if i < courant else ("courante" if i == courant else "")) for i, (lib, _) in enumerate(ordre)]


@login_required
@require_POST
def demande_action(request, pk, action):
    d = _demande_visible(request, pk)
    u = request.user
    if action == "soumettre":
        if not (d.statut == S.BROUILLON and (d.demandeur_id == u.id or u.est_admin) and d.lignes.exists()):
            raise PermissionDenied
        d.statut, d.motif_rejet = S.SOUMISE, ""
        d.save()
        _historiser(d, u, "Soumise pour validation hiérarchique")
    elif action == "valider":
        if not d.peut_etre_valide_par(u):
            raise PermissionDenied
        d.statut = S.VALIDEE
        d.save()
        _historiser(d, u, "Validée par la hiérarchie → arbitrage budgétaire")
    elif action == "annuler":
        if not (d.statut in (S.BROUILLON, S.SOUMISE, S.VALIDEE) and (d.demandeur_id == u.id or u.peut_arbitrer)):
            raise PermissionDenied
        d.statut = S.ANNULEE
        d.save()
        _historiser(d, u, "Annulée")
    elif action == "cloturer":
        if not (d.statut == S.SERVIE_PARTIEL and (u.peut_gerer_stock or u.peut_arbitrer)):
            raise PermissionDenied
        d.statut = S.SERVIE
        d.save()
        _historiser(d, u, "Clôturée (reliquat non servi abandonné)")
    else:
        raise PermissionDenied
    messages.success(request, f"Statut : {d.get_statut_display()}.")
    return redirect("demande_detail", pk=d.pk)


@login_required
def demande_rejeter(request, pk):
    d = _demande_visible(request, pk)
    if not d.peut_etre_valide_par(request.user):
        raise PermissionDenied
    form = f.RejetForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d.statut, d.motif_rejet = S.REJETEE, form.cleaned_data["motif"]
        d.save()
        _historiser(d, request.user, "Rejetée par la hiérarchie", d.motif_rejet)
        messages.success(request, "Demande rejetée.")
        return redirect("demande_detail", pk=d.pk)
    return render(request, "gestion/formulaire.html", {"form": form, "titre_page": f"Rejeter {d.reference}"})


@role_requis("peut_arbitrer")
def demande_arbitrer(request, pk):
    d = _demande_visible(request, pk)
    if d.statut != S.VALIDEE:
        raise PermissionDenied
    formset = f.ArbitrageFormSet(request.POST or None, instance=d)
    decision = f.ArbitrageDecisionForm(request.POST or None)
    budget = _budget_info(d)
    if request.method == "POST" and decision.is_valid():
        dec = decision.cleaned_data
        commentaire = dec["commentaire"].strip()
        if dec["decision"] == "rejeter":
            if not commentaire:
                decision.add_error("commentaire", "Un motif est obligatoire pour rejeter.")
            else:
                d.statut, d.motif_rejet = S.REJETEE, commentaire
                d.save()
                _historiser(d, request.user, "Rejetée à l'arbitrage budgétaire", commentaire)
                messages.success(request, "Demande rejetée.")
                return redirect("demande_detail", pk=d.pk)
        elif formset.is_valid():
            total = sum(fm.cleaned_data["quantite_accordee"] * fm.instance.prix_unitaire for fm in formset.forms)
            if total == 0:
                decision.add_error(None, "Aucune quantité accordée : utilisez « Rejeter la demande ».")
            elif total > budget["disponible"] and not dec["autoriser_depassement"]:
                decision.add_error(None, "Le montant accordé dépasse le disponible de l'enveloppe. "
                                         "Réduisez les quantités ou autorisez explicitement le dépassement.")
            elif total > budget["disponible"] and not commentaire:
                decision.add_error("commentaire", "Un commentaire est obligatoire pour justifier un dépassement.")
            else:
                with transaction.atomic():
                    formset.save()
                    d.statut = S.APPROUVEE
                    d.commentaire_arbitrage = commentaire
                    d.depassement_autorise = total > budget["disponible"]
                    d.save()
                    _historiser(d, request.user,
                                f"Approuvée à l'arbitrage ({int(total):,} GNF accordés)".replace(",", " "),
                                commentaire)
                messages.success(request, "Demande approuvée : elle peut être servie par le magasin.")
                return redirect("demande_detail", pk=d.pk)
    return render(request, "gestion/arbitrage.html", {"d": d, "formset": formset, "decision": decision, "budget": budget})


@role_requis("peut_gerer_stock")
def demande_servir(request, pk):
    d = _demande_visible(request, pk)
    if d.statut not in (S.APPROUVEE, S.SERVIE_PARTIEL):
        raise PermissionDenied
    form = f.ServirForm(request.POST or None, demande=d)
    if not form.noms:
        messages.info(request, "Tout a déjà été servi.")
        return redirect("demande_detail", pk=d.pk)
    if request.method == "POST" and form.is_valid():
        a_servir = [(l, form.cleaned_data[nom]) for l, nom in form.noms if form.cleaned_data[nom] > 0]
        if not a_servir:
            form.add_error(None, "Indiquez au moins une quantité à servir.")
        else:
            try:
                with transaction.atomic():
                    bon = BonSortie.objects.create(demande=d, magasinier=request.user,
                                                   observations=form.cleaned_data["observations"])
                    for l, q in a_servir:
                        mouvement(l.article, T.SORTIE, q, request.user, structure=d.structure, demande=d,
                                  bon_sortie=bon, motif=f"Bon de sortie {bon.reference}", prix=l.prix_unitaire)
                        LigneBonSortie.objects.create(bon=bon, article=l.article, quantite=q,
                                                      prix_unitaire=l.prix_unitaire)
                        l.quantite_servie += q
                        l.save(update_fields=["quantite_servie"])
                        if l.article.est_equipement:
                            dispo = list(Equipement.objects.filter(article=l.article,
                                                                   statut=Equipement.Statut.EN_STOCK).order_by("pk")[:q])
                            if len(dispo) < q:
                                raise StockInsuffisant(f"Équipements inventoriés insuffisants pour « {l.article.designation} ».")
                            for eq in dispo:
                                eq.affecter(d.structure, d.demandeur.get_full_name() or d.demandeur.username, "",
                                            request.user, f"Distribution {bon.reference}", demande=d)
                    d.refresh_from_db()
                    d.statut = S.SERVIE if d.reste_a_servir == 0 else S.SERVIE_PARTIEL
                    d.save()
                    _historiser(d, request.user, f"Sortie de stock {bon.reference}",
                                "; ".join(f"{q} × {l.article.designation}" for l, q in a_servir))
            except StockInsuffisant as e:
                form.add_error(None, str(e))
            else:
                messages.success(request, f"Bon de sortie {bon.reference} enregistré.")
                return redirect("bon_sortie", pk=bon.pk)
    return render(request, "gestion/servir.html", {"d": d, "form": form})


@login_required
def bon_sortie(request, pk):
    bon = get_object_or_404(BonSortie.objects.select_related("demande__structure", "magasinier"), pk=pk)
    _demande_visible(request, bon.demande_id)
    return render(request, "gestion/bon_sortie.html", {"bon": bon, "lignes": bon.lignes.select_related("article")})


# ================================================================ Stock
@login_required
def stock(request):
    qs = Article.objects.select_related("categorie")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(designation__icontains=q) | Q(reference__icontains=q))
    type_a = request.GET.get("type", "")
    if type_a:
        qs = qs.filter(type_article=type_a)
    articles = list(qs)
    if request.GET.get("alertes"):
        articles = [a for a in articles if a.niveau[0] != "OK"]
    valeur_totale = sum((a.valeur_stock for a in articles), Decimal(0))
    return render(request, "gestion/stock.html", {"articles": articles, "valeur_totale": valeur_totale,
                                                  "q": q, "type_sel": type_a,
                                                  "alertes": request.GET.get("alertes", ""),
                                                  "types": Article.Type.choices})


@login_required
def mouvements(request):
    qs = MouvementStock.objects.select_related("article", "structure", "utilisateur")
    if not request.user.vue_globale:
        qs = qs.filter(structure_id__in=request.user.structures_gerees_ids())
    for champ in ("article", "type_mvt"):
        if request.GET.get(champ):
            qs = qs.filter(**{champ: request.GET[champ]})
    page, params = _page(request, qs, 40)
    return render(request, "gestion/mouvements.html", {
        "page": page, "params": params, "types": T.choices, "articles": Article.objects.all(),
        "article_sel": request.GET.get("article", ""), "type_sel": request.GET.get("type_mvt", "")})


@role_requis("peut_gerer_stock")
def perte(request):
    form = f.PerteForm(request.POST or None, initial={"article": request.GET.get("article")})
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            mouvement(cd["article"], T.PERTE, cd["quantite"], request.user,
                      motif=f"{cd['motif']}" + (f" – {cd['commentaire']}" if cd["commentaire"] else ""))
        except StockInsuffisant as e:
            form.add_error(None, str(e))
        else:
            messages.success(request, "Perte enregistrée.")
            return redirect("stock")
    return render(request, "gestion/formulaire.html", {"form": form, "titre_page": "Déclarer une perte / casse / péremption"})


@role_requis("peut_gerer_stock")
def ajustement(request):
    form = f.AjustementForm(request.POST or None, initial={"article": request.GET.get("article")})
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        ecart = cd["quantite_reelle"] - cd["article"].stock
        if ecart == 0:
            messages.info(request, "Aucun écart : le stock théorique est exact.")
        else:
            mouvement(cd["article"], T.AJUSTEMENT, ecart, request.user,
                      motif=f"Inventaire : {cd['motif']} (écart {ecart:+d})")
            messages.success(request, f"Stock ajusté (écart {ecart:+d}).")
        return redirect("stock")
    return render(request, "gestion/formulaire.html", {"form": form, "titre_page": "Ajustement d'inventaire"})


@role_requis("peut_gerer_catalogue")
def article_form(request, pk=None):
    article = get_object_or_404(Article, pk=pk) if pk else None
    form = f.ArticleForm(request.POST or None, instance=article)
    if request.method == "POST" and form.is_valid():
        a = form.save()
        messages.success(request, f"Article {a.reference} enregistré.")
        return redirect("stock")
    return render(request, "gestion/formulaire.html",
                  {"form": form, "titre_page": "Modifier l'article" if article else "Nouvel article"})


# ================================================================ Fournisseurs / commandes
@role_requis("vue_globale")
def fournisseurs(request):
    return render(request, "gestion/fournisseurs.html", {"fournisseurs": Fournisseur.objects.annotate(nb=Count("commandes"))})


@role_requis("peut_commander")
def fournisseur_form(request, pk=None):
    obj = get_object_or_404(Fournisseur, pk=pk) if pk else None
    form = f.FournisseurForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Fournisseur enregistré.")
        return redirect("fournisseurs")
    return render(request, "gestion/formulaire.html",
                  {"form": form, "titre_page": "Modifier le fournisseur" if obj else "Nouveau fournisseur"})


@role_requis("vue_globale")
def commandes(request):
    qs = Commande.objects.select_related("fournisseur")
    if request.GET.get("statut"):
        qs = qs.filter(statut=request.GET["statut"])
    page, params = _page(request, qs)
    return render(request, "gestion/commandes.html", {"page": page, "params": params,
                                                      "statuts": Commande.Statut.choices,
                                                      "statut_sel": request.GET.get("statut", "")})


@role_requis("vue_globale")
def commande_detail(request, pk):
    c = get_object_or_404(Commande.objects.select_related("fournisseur"), pk=pk)
    return render(request, "gestion/commande_detail.html", {
        "c": c, "lignes": c.lignes.select_related("article"), "livraisons": c.livraisons.select_related("recu_par"),
        "modifiable": c.statut == Commande.Statut.BROUILLON and request.user.peut_commander})


@role_requis("peut_commander")
def commande_form(request, pk=None):
    commande = get_object_or_404(Commande, pk=pk, statut=Commande.Statut.BROUILLON) if pk else Commande()
    initial = []
    if not pk:  # préremplissage depuis le plan d'achat : ?a=ID:QTE&a=ID:QTE
        for item in request.GET.getlist("a"):
            try:
                aid, qte = item.split(":")
                initial.append({"article": int(aid), "quantite": int(qte)})
            except ValueError:
                continue
    FS = f.formset_commande(extra=max(len(initial), 0) + 2 if not pk else 2)
    form = f.CommandeForm(request.POST or None, instance=commande)
    formset = FS(request.POST or None, instance=commande, initial=initial if not pk else None)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            c = form.save(commit=False)
            if not c.pk:
                c.cree_par = request.user
            c.save()
            formset.instance = c
            formset.save()
        messages.success(request, f"Commande {c.reference} enregistrée en brouillon.")
        return redirect("commande_detail", pk=c.pk)
    return render(request, "gestion/commande_form.html",
                  {"form": form, "formset": formset, "titre_page": "Modifier la commande" if pk else "Nouvelle commande"})


@role_requis("peut_commander")
@require_POST
def commande_action(request, pk, action):
    c = get_object_or_404(Commande, pk=pk)
    CS = Commande.Statut
    if action == "envoyer" and c.statut == CS.BROUILLON and c.lignes.exists():
        c.statut = CS.ENVOYEE
    elif action == "annuler" and c.statut in (CS.BROUILLON, CS.ENVOYEE) and not c.livraisons.exists():
        c.statut = CS.ANNULEE
    else:
        raise PermissionDenied
    c.save()
    messages.success(request, f"Commande {c.reference} : {c.get_statut_display()}.")
    return redirect("commande_detail", pk=c.pk)


@role_requis("peut_gerer_stock")
def reception(request, pk):
    cmd = get_object_or_404(Commande, pk=pk, statut__in=[Commande.Statut.ENVOYEE, Commande.Statut.PARTIELLE])
    form = f.ReceptionForm(request.POST or None, commande=cmd)
    if request.method == "POST" and form.is_valid():
        recues = [(l, form.cleaned_data[nom]) for l, nom in form.noms if form.cleaned_data[nom] > 0]
        if not recues:
            form.add_error(None, "Indiquez au moins une quantité reçue.")
        else:
            with transaction.atomic():
                liv = Livraison.objects.create(
                    commande=cmd, numero_bl=form.cleaned_data["numero_bl"], recu_par=request.user,
                    date_livraison=form.cleaned_data["date_livraison"], observations=form.cleaned_data["observations"])
                for l, q in recues:
                    LigneLivraison.objects.create(livraison=liv, ligne_commande=l, quantite=q)
                    mouvement(l.article, T.ENTREE, q, request.user, livraison=liv,
                              motif=f"Livraison {liv.reference} ({cmd.reference})", prix=l.prix_unitaire)
                    l.quantite_recue += q
                    l.save(update_fields=["quantite_recue"])
                    if l.article.est_equipement:
                        for _ in range(q):  # un numéro d'inventaire par unité
                            Equipement.objects.create(article=l.article, date_acquisition=liv.date_livraison,
                                                      valeur_acquisition=l.prix_unitaire or 0, livraison=liv)
                cmd.maj_statut_livraison()
            messages.success(request, f"Livraison {liv.reference} enregistrée : stock et inventaire mis à jour.")
            return redirect("commande_detail", pk=cmd.pk)
    return render(request, "gestion/reception.html", {"c": cmd, "form": form})


@role_requis("vue_globale")
def livraisons(request):
    page, params = _page(request, Livraison.objects.select_related("commande__fournisseur", "recu_par"))
    return render(request, "gestion/livraisons.html", {"page": page, "params": params})


# ================================================================ Équipements
def _equipements_visibles(user):
    qs = Equipement.objects.select_related("article", "structure")
    return qs if user.vue_globale else qs.filter(structure_id__in=user.structures_gerees_ids())


@login_required
def equipements(request):
    qs = _equipements_visibles(request.user)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(numero_serie__icontains=q) | Q(article__designation__icontains=q)
                       | Q(beneficiaire__icontains=q))
    for champ in ("statut", "etat", "structure"):
        if request.GET.get(champ):
            qs = qs.filter(**{champ: request.GET[champ]})
    page, params = _page(request, qs)
    return render(request, "gestion/equipements.html", {
        "page": page, "params": params, "q": q, "statuts": Equipement.Statut.choices, "etats": Equipement.Etat.choices,
        "structures": Structure.objects.all(), "sel": {k: request.GET.get(k, "") for k in ("statut", "etat", "structure")}})


@login_required
def equipement_detail(request, pk):
    eq = get_object_or_404(_equipements_visibles(request.user), pk=pk)
    return render(request, "gestion/equipement_detail.html", {
        "eq": eq, "affectations": eq.affectations.select_related("structure", "cree_par"),
        "motif_form": f.MotifForm()})


@role_requis("peut_gerer_stock")
def equipement_creer(request):
    form = f.EquipementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        with transaction.atomic():
            eq = form.save()
            if cd["statut_initial"] == "AFFECTE":
                eq.affecter(cd["structure_affectation"], eq.beneficiaire, eq.localisation, request.user,
                            "Saisie d'un équipement existant")
            else:
                mouvement(eq.article, T.AJUSTEMENT, 1, request.user, motif=f"Inventaire initial {eq.reference}")
        messages.success(request, f"Équipement {eq.reference} enregistré.")
        return redirect("equipement_detail", pk=eq.pk)
    return render(request, "gestion/formulaire.html", {"form": form, "titre_page": "Enregistrer un équipement existant"})


@role_requis("peut_gerer_stock")
def equipement_affecter(request, pk):
    eq = get_object_or_404(Equipement, pk=pk)
    if eq.statut in (Equipement.Statut.REFORME, Equipement.Statut.EN_REPARATION):
        raise PermissionDenied
    form = f.AffectationForm(request.POST or None, initial={"structure": eq.structure_id, "beneficiaire": eq.beneficiaire,
                                                            "localisation": eq.localisation})
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        with transaction.atomic():
            if eq.statut == Equipement.Statut.EN_STOCK:
                mouvement(eq.article, T.SORTIE, 1, request.user, structure=cd["structure"],
                          motif=f"Affectation directe {eq.reference}", prix=eq.valeur_acquisition)
            eq.affecter(cd["structure"], cd["beneficiaire"], cd["localisation"], request.user, cd["motif"])
        messages.success(request, "Affectation enregistrée.")
        return redirect("equipement_detail", pk=eq.pk)
    return render(request, "gestion/formulaire.html", {"form": form, "titre_page": f"Affecter / transférer {eq.reference}"})


@role_requis("peut_gerer_stock")
@require_POST
def equipement_action(request, pk, action):
    eq = get_object_or_404(Equipement, pk=pk)
    ES = Equipement.Statut
    motif = request.POST.get("motif", "").strip()
    with transaction.atomic():
        if action == "restituer" and eq.statut == ES.AFFECTE:
            eq.restituer()
            mouvement(eq.article, T.RETOUR, 1, request.user, motif=f"Restitution {eq.reference} {motif}".strip(),
                      prix=eq.valeur_acquisition)
        elif action == "reparation" and eq.statut == ES.AFFECTE:
            eq.statut, eq.etat = ES.EN_REPARATION, Equipement.Etat.DEFECTUEUX
            eq.save()
        elif action == "service" and eq.statut == ES.EN_REPARATION:
            eq.statut, eq.etat = ES.AFFECTE, Equipement.Etat.BON
            eq.save()
        elif action == "reformer" and eq.statut != ES.REFORME:
            if not motif:
                messages.error(request, "Un motif est obligatoire pour réformer un équipement.")
                return redirect("equipement_detail", pk=eq.pk)
            en_stock = eq.statut == ES.EN_STOCK
            eq.reformer()
            if en_stock:
                mouvement(eq.article, T.PERTE, 1, request.user, motif=f"Réforme {eq.reference} : {motif}",
                          prix=eq.valeur_acquisition)
        else:
            raise PermissionDenied
    messages.success(request, f"Équipement {eq.reference} : {eq.get_statut_display()}.")
    return redirect("equipement_detail", pk=eq.pk)


# ================================================================ Budgets
@role_requis("vue_globale")
def budgets(request):
    try:
        exercice = int(request.GET.get("exercice", annee_courante()))
    except ValueError:
        exercice = annee_courante()
    lignes = [{"e": e, "engage": e.engage, "taux": e.taux_engage}
              for e in EnveloppeBudgetaire.objects.filter(exercice=exercice).select_related("structure")]
    total = sum((e.montant_alloue for e in EnveloppeBudgetaire.objects.filter(exercice=exercice)), Decimal(0))
    return render(request, "gestion/budgets.html", {"lignes": lignes, "exercice": exercice, "total": total,
                                                    "peut_modifier": request.user.peut_arbitrer})


@role_requis("peut_arbitrer")
def enveloppe_form(request, pk=None):
    obj = get_object_or_404(EnveloppeBudgetaire, pk=pk) if pk else None
    form = f.EnveloppeForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        e = form.save()
        messages.success(request, "Enveloppe enregistrée.")
        return redirect(f"/budgets/?exercice={e.exercice}")
    return render(request, "gestion/formulaire.html",
                  {"form": form, "titre_page": "Modifier l'enveloppe" if obj else "Nouvelle enveloppe budgétaire"})


# ================================================================ Rapports
def _filtre_periode(request, qs, champ_date="date"):
    form = f.FiltreRapportForm(request.GET or None, user=request.user)
    exercice, structure, du, au = None, None, None, None
    if form.is_valid():
        exercice, structure = form.cleaned_data["exercice"], form.cleaned_data["structure"]
        du, au = form.cleaned_data["du"], form.cleaned_data["au"]
    if not (du or au) and not exercice:
        exercice = annee_courante()
    if exercice and not (du or au):
        qs = qs.filter(**{f"{champ_date}__year": exercice})
    if du:
        qs = qs.filter(**{f"{champ_date}__date__gte": du})
    if au:
        qs = qs.filter(**{f"{champ_date}__date__lte": au})
    if structure:
        qs = qs.filter(structure=structure)
    return qs, form, exercice


@login_required
def rapport_consommation(request):
    qs = MouvementStock.objects.filter(type_mvt=T.SORTIE)
    if not request.user.vue_globale:
        qs = qs.filter(structure_id__in=request.user.structures_gerees_ids())
    qs, form, exercice = _filtre_periode(request, qs)

    if request.GET.get("export") == "csv":
        return _csv("consommation.csv", ["Date", "Direction / Commission", "Article", "Quantité", "Valeur (GNF)", "Référence"],
                    [(m.date.strftime("%d/%m/%Y"), m.structure or "", m.article.designation, abs(m.quantite),
                      int(m.valeur), m.motif) for m in qs.select_related("article", "structure")])

    def barres(lignes):
        maxi = max((l["valeur"] for l in lignes), default=0) or 1
        for l in lignes:
            l["pct"] = int(l["valeur"] * 100 / maxi)
        return lignes

    par_structure = barres([{"nom": r["structure__nom"] or "—", "qte": -r["q"], "valeur": r["v"]} for r in
                            qs.values("structure__nom").annotate(q=Sum("quantite"), v=Sum("valeur")).order_by("-v")])
    par_article = barres([{"nom": r["article__designation"], "qte": -r["q"], "valeur": r["v"]} for r in
                          qs.values("article__designation").annotate(q=Sum("quantite"), v=Sum("valeur")).order_by("-v")[:15]])
    par_mois = barres([{"nom": r["m"].strftime("%m/%Y"), "qte": -r["q"], "valeur": r["v"]} for r in
                       qs.annotate(m=TruncMonth("date")).values("m").annotate(q=Sum("quantite"), v=Sum("valeur")).order_by("m")])
    total = qs.aggregate(v=Sum("valeur"))["v"] or 0
    return render(request, "gestion/rapport_consommation.html", {
        "form": form, "exercice": exercice, "par_structure": par_structure, "par_article": par_article,
        "par_mois": par_mois, "total": total, "query": request.GET.urlencode()})


@role_requis("vue_globale")
def rapport_budget(request):
    try:
        exercice = int(request.GET.get("exercice", annee_courante()))
    except ValueError:
        exercice = annee_courante()
    consomme = {r["structure"]: r["v"] for r in MouvementStock.objects.filter(
        type_mvt=T.SORTIE, date__year=exercice).values("structure").annotate(v=Sum("valeur"))}
    lignes, tot = [], {"alloue": Decimal(0), "engage": Decimal(0), "consomme": Decimal(0)}
    ids = set(EnveloppeBudgetaire.objects.filter(exercice=exercice).values_list("structure_id", flat=True))
    ids |= set(Demande.objects.filter(exercice=exercice, statut__in=Demande.STATUTS_ENGAGES).values_list("structure_id", flat=True))
    for s in Structure.objects.filter(pk__in=ids):
        env = EnveloppeBudgetaire.objects.filter(exercice=exercice, structure=s).first()
        alloue = env.montant_alloue if env else Decimal(0)
        engage = Demande.engage(exercice, s)
        conso = consomme.get(s.pk) or Decimal(0)
        taux = int(engage * 100 / alloue) if alloue else (100 if engage else 0)
        lignes.append({"structure": s, "alloue": alloue, "engage": engage, "consomme": conso,
                       "disponible": alloue - engage, "taux": taux, "classe": "depasse" if taux > 100 else ("alerte" if taux >= 85 else "")})
        tot["alloue"] += alloue
        tot["engage"] += engage
        tot["consomme"] += conso
    lignes.sort(key=lambda l: -l["taux"])
    return render(request, "gestion/rapport_budget.html", {"lignes": lignes, "tot": tot, "exercice": exercice})


@role_requis("vue_globale")
def rapport_pertes(request):
    qs = MouvementStock.objects.filter(Q(type_mvt=T.PERTE) | Q(type_mvt=T.AJUSTEMENT, quantite__lt=0))
    qs, form, exercice = _filtre_periode(request, qs)
    qs = qs.select_related("article", "utilisateur")
    total = qs.aggregate(v=Sum("valeur"))["v"] or 0
    par_cause = list(qs.values("motif").annotate(n=Count("pk"), v=Sum("valeur")).order_by("-v")[:10])
    return render(request, "gestion/rapport_pertes.html", {"form": form, "exercice": exercice, "mouvements": qs[:200],
                                                           "total": total, "par_cause": par_cause})


@role_requis("vue_globale")
def planification(request):
    lignes = plan_achats()
    if request.GET.get("export") == "csv":
        return _csv("plan_achats.csv", ["Article", "Stock", "Besoins à servir", "En commande", "Stock projeté",
                                        "Seuil", "À commander", "Montant estimé (GNF)"],
                    [(l["article"].designation, l["stock"], l["besoins"], l["en_commande"], l["projete"],
                      l["seuil"], l["a_commander"], int(l["montant"])) for l in lignes])
    total = sum((l["montant"] for l in lignes), Decimal(0))
    lien = "&".join(f"a={l['article'].pk}:{l['a_commander']}" for l in lignes)
    return render(request, "gestion/planification.html", {"lignes": lignes, "total": total, "lien_commande": lien})
