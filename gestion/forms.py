from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from comptes.models import Structure

from .models import (Article, Commande, Demande, EnveloppeBudgetaire, Equipement, Fournisseur,
                     LigneCommande, LigneDemande, MouvementStock)


class StyleMixin:
    def appliquer_style(self):
        for champ in self.fields.values():
            w = champ.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs["class"] = "form-check-input"
            elif isinstance(w, forms.RadioSelect):
                pass
            elif isinstance(w, (forms.Select, forms.SelectMultiple)):
                w.attrs["class"] = "form-select"
            else:
                w.attrs["class"] = "form-control"


class StyledForm(StyleMixin, forms.Form):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.appliquer_style()


class StyledModelForm(StyleMixin, forms.ModelForm):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.appliquer_style()


DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


# ---------------------------------------------------------------- Demandes
class DemandeForm(StyledModelForm):
    class Meta:
        model = Demande
        fields = ["structure", "exercice", "objet", "priorite", "date_souhaitee", "justification"]
        widgets = {"date_souhaitee": DATE, "justification": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *a, user, **kw):
        super().__init__(*a, **kw)
        if user.est_admin:
            qs = Structure.objects.all()
        else:
            qs = Structure.objects.filter(pk__in=user.structures_gerees_ids())
        self.fields["structure"].queryset = qs
        self.fields["structure"].empty_label = None
        if not self.instance.pk and user.structure_id:
            self.fields["structure"].initial = user.structure_id
        self.appliquer_style()


class LigneDemandeForm(StyledModelForm):
    class Meta:
        model = LigneDemande
        fields = ["article", "quantite_demandee", "justification"]
        widgets = {"quantite_demandee": forms.NumberInput(attrs={"min": 1})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["article"].queryset = Article.objects.filter(actif=True).select_related("categorie")
        self.fields["quantite_demandee"].min_value = 1
        self.appliquer_style()

    def clean_quantite_demandee(self):
        q = self.cleaned_data["quantite_demandee"]
        if q < 1:
            raise ValidationError("La quantité doit être d'au moins 1.")
        return q


class BaseLignesFormSet(forms.BaseInlineFormSet):
    """Interdit le même article deux fois dans un document."""

    def clean(self):
        super().clean()
        vus = set()
        for f in self.forms:
            cd = getattr(f, "cleaned_data", None)
            if not cd or cd.get("DELETE") or not cd.get("article"):
                continue
            if cd["article"].pk in vus:
                raise ValidationError("Un même article ne peut figurer qu'une seule fois : regroupez les quantités.")
            vus.add(cd["article"].pk)


def formset_demande(extra=4):
    return forms.inlineformset_factory(Demande, LigneDemande, form=LigneDemandeForm, formset=BaseLignesFormSet,
                                       extra=extra, min_num=1, validate_min=True, can_delete=True)


class ArbitrageLigneForm(StyledModelForm):
    class Meta:
        model = LigneDemande
        fields = ["quantite_accordee"]
        widgets = {"quantite_accordee": forms.NumberInput(attrs={"min": 0})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["quantite_accordee"].required = True
        if self.instance.quantite_accordee is None:
            self.initial["quantite_accordee"] = self.instance.quantite_demandee
        self.appliquer_style()

    def clean_quantite_accordee(self):
        q = self.cleaned_data["quantite_accordee"]
        if q > self.instance.quantite_demandee:
            raise ValidationError("Ne peut dépasser la quantité demandée.")
        return q


ArbitrageFormSet = forms.inlineformset_factory(Demande, LigneDemande, form=ArbitrageLigneForm,
                                               extra=0, can_delete=False)


class ArbitrageDecisionForm(StyledForm):
    decision = forms.ChoiceField(label="Décision", widget=forms.RadioSelect,
                                 choices=[("approuver", "Approuver avec les quantités ci-dessus"),
                                          ("rejeter", "Rejeter la demande")], initial="approuver")
    commentaire = forms.CharField(label="Commentaire d'arbitrage / motif du rejet", required=False,
                                  widget=forms.Textarea(attrs={"rows": 3}))
    autoriser_depassement = forms.BooleanField(
        label="Autoriser un dépassement de l'enveloppe (commentaire obligatoire)", required=False)


class RejetForm(StyledForm):
    motif = forms.CharField(label="Motif du rejet", widget=forms.Textarea(attrs={"rows": 3}))


class ServirForm(StyledForm):
    observations = forms.CharField(label="Observations", required=False, max_length=250)

    def __init__(self, *a, demande, **kw):
        super().__init__(*a, **kw)
        self.rows, self.noms = [], []
        for l in demande.lignes.select_related("article"):
            if l.reste_a_servir <= 0:
                continue
            maxi = min(l.reste_a_servir, max(l.article.stock, 0))
            nom = f"ligne_{l.pk}"
            self.fields[nom] = forms.IntegerField(label=str(l.article), min_value=0, max_value=maxi,
                                                  initial=maxi, required=True)
            self.fields[nom].widget.attrs.update({"class": "form-control", "min": 0, "max": maxi})
            self.noms.append((l, nom))
        self.rows = [(l, self[nom]) for l, nom in self.noms]


# ---------------------------------------------------------------- Catalogue / budget
class ArticleForm(StyledModelForm):
    class Meta:
        model = Article
        fields = ["designation", "categorie", "type_article", "unite", "prix_reference",
                  "seuil_alerte", "stock_max", "actif"]


class FournisseurForm(StyledModelForm):
    class Meta:
        model = Fournisseur
        fields = ["nom", "identifiant_fiscal", "contact", "telephone", "email", "adresse", "actif"]


class EnveloppeForm(StyledModelForm):
    class Meta:
        model = EnveloppeBudgetaire
        fields = ["exercice", "structure", "montant_alloue", "observations"]


# ---------------------------------------------------------------- Commandes
class CommandeForm(StyledModelForm):
    class Meta:
        model = Commande
        fields = ["fournisseur", "date_commande", "date_livraison_prevue", "observations"]
        widgets = {"date_commande": DATE, "date_livraison_prevue": DATE,
                   "observations": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["fournisseur"].queryset = Fournisseur.objects.filter(actif=True)
        self.appliquer_style()


class LigneCommandeForm(StyledModelForm):
    class Meta:
        model = LigneCommande
        fields = ["article", "quantite", "prix_unitaire"]
        widgets = {"quantite": forms.NumberInput(attrs={"min": 1})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["article"].queryset = Article.objects.filter(actif=True)
        self.fields["prix_unitaire"].required = False
        self.fields["prix_unitaire"].help_text = "Vide = prix de référence"
        self.appliquer_style()


def formset_commande(extra=3):
    return forms.inlineformset_factory(Commande, LigneCommande, form=LigneCommandeForm, formset=BaseLignesFormSet,
                                       extra=extra, min_num=1, validate_min=True, can_delete=True)


class ReceptionForm(StyledForm):
    numero_bl = forms.CharField(label="N° du bon de livraison fournisseur", required=False, max_length=60)
    date_livraison = forms.DateField(label="Date de livraison", widget=DATE, initial=timezone.localdate)
    observations = forms.CharField(label="Observations (réserves, anomalies)", required=False,
                                   widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *a, commande, **kw):
        super().__init__(*a, **kw)
        self.noms = []
        for l in commande.lignes.select_related("article"):
            if l.reste <= 0:
                continue
            nom = f"ligne_{l.pk}"
            self.fields[nom] = forms.IntegerField(label=str(l.article), min_value=0, max_value=l.reste,
                                                  initial=l.reste)
            self.fields[nom].widget.attrs.update({"class": "form-control", "min": 0, "max": l.reste})
            self.noms.append((l, nom))
        self.rows = [(l, self[nom]) for l, nom in self.noms]


# ---------------------------------------------------------------- Stock
class PerteForm(StyledForm):
    MOTIFS = [("Casse", "Casse"), ("Péremption", "Péremption"), ("Vol / disparition", "Vol / disparition"),
              ("Détérioration", "Détérioration (humidité, nuisibles…)"), ("Autre", "Autre")]
    article = forms.ModelChoiceField(label="Article", queryset=Article.objects.none())
    quantite = forms.IntegerField(label="Quantité perdue", min_value=1)
    motif = forms.ChoiceField(label="Cause", choices=MOTIFS)
    commentaire = forms.CharField(label="Précisions", required=False, max_length=200)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["article"].queryset = Article.objects.filter(type_article=Article.Type.CONSOMMABLE, actif=True)
        self.appliquer_style()


class AjustementForm(StyledForm):
    article = forms.ModelChoiceField(label="Article", queryset=Article.objects.none())
    quantite_reelle = forms.IntegerField(label="Quantité réellement comptée", min_value=0)
    motif = forms.CharField(label="Motif / référence de l'inventaire", max_length=200)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["article"].queryset = Article.objects.filter(type_article=Article.Type.CONSOMMABLE, actif=True)
        self.appliquer_style()


# ---------------------------------------------------------------- Équipements
class EquipementForm(StyledModelForm):
    statut_initial = forms.ChoiceField(
        label="Situation actuelle", choices=[("STOCK", "En stock (non affecté)"), ("AFFECTE", "Déjà affecté")])
    structure_affectation = forms.ModelChoiceField(label="Affecté à", queryset=Structure.objects.all(), required=False)

    class Meta:
        model = Equipement
        fields = ["article", "numero_serie", "date_acquisition", "valeur_acquisition", "etat",
                  "beneficiaire", "localisation"]
        widgets = {"date_acquisition": DATE}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["article"].queryset = Article.objects.filter(type_article=Article.Type.EQUIPEMENT, actif=True)
        self.appliquer_style()

    def clean(self):
        cd = super().clean()
        if cd.get("statut_initial") == "AFFECTE" and not cd.get("structure_affectation"):
            self.add_error("structure_affectation", "Indiquez la direction / commission bénéficiaire.")
        return cd


class AffectationForm(StyledForm):
    structure = forms.ModelChoiceField(label="Direction / Commission", queryset=Structure.objects.all())
    beneficiaire = forms.CharField(label="Bénéficiaire (agent)", max_length=150, required=False)
    localisation = forms.CharField(label="Localisation (bureau, salle)", max_length=150, required=False)
    motif = forms.CharField(label="Motif", max_length=250, required=False)


class MotifForm(StyledForm):
    motif = forms.CharField(label="Motif", max_length=250)


# ---------------------------------------------------------------- Filtres
class FiltreDemandeForm(StyledForm):
    q = forms.CharField(label="Recherche", required=False,
                        widget=forms.TextInput(attrs={"placeholder": "Référence, objet…"}))
    statut = forms.ChoiceField(label="Statut", required=False, choices=[("", "Tous les statuts")] + Demande.Statut.choices)
    priorite = forms.ChoiceField(label="Priorité", required=False, choices=[("", "Toutes priorités")] + Demande.Priorite.choices)
    structure = forms.ModelChoiceField(label="Direction", required=False, queryset=Structure.objects.all(),
                                       empty_label="Toutes les directions")
    exercice = forms.IntegerField(label="Exercice", required=False, widget=forms.NumberInput(attrs={"placeholder": "Exercice"}))
    du = forms.DateField(label="Du", required=False, widget=DATE)
    au = forms.DateField(label="Au", required=False, widget=DATE)


class FiltreRapportForm(StyledForm):
    exercice = forms.IntegerField(label="Exercice", required=False)
    structure = forms.ModelChoiceField(label="Direction / Commission", required=False, queryset=Structure.objects.none(),
                                       empty_label="Toutes")
    du = forms.DateField(label="Du", required=False, widget=DATE)
    au = forms.DateField(label="Au", required=False, widget=DATE)

    def __init__(self, *a, user=None, **kw):
        super().__init__(*a, **kw)
        if user is not None and not user.vue_globale:
            self.fields["structure"].queryset = Structure.objects.filter(pk__in=user.structures_gerees_ids())
        else:
            self.fields["structure"].queryset = Structure.objects.all()
        self.appliquer_style()
