# Gestion des besoins en matériels et consommables – ANG (République de Guinée)

Application Django pour **maîtriser les dépenses, réduire les pertes et mieux planifier les achats**.

## Circuit
1. **Expression des besoins** par les directions et commissions (lignes d'articles, priorité, justification).
2. **Validation hiérarchique** (responsable de direction / président de commission, y compris sous-structures) puis
   **arbitrage budgétaire** par le Secrétariat général / DAF : quantités accordées ligne par ligne, contrôle de
   l'enveloppe disponible, dépassement possible seulement avec décision explicite et commentaire.
3. **Stock** : bon de sortie imprimable, mouvements tracés, seuils d'alerte, pertes/casses/péremptions, inventaire avec ajustement.
4. **Achats** : fournisseurs, commandes, **réception des livraisons** (totales ou partielles, bon de livraison) qui alimente le stock.
5. **Équipements** : un n° d'inventaire par unité reçue, affectation / transfert / restitution / réparation / réforme, historique complet.
6. **Rapports** : historique des demandes, consommation (par direction, article, mois, export CSV), exécution budgétaire,
   pertes et écarts, **plan d'achat suggéré** (stock projeté = stock + commandes en cours − besoins approuvés).

## Rôles
Administrateur · Agent demandeur · Responsable (directeur / président de commission) · Arbitre budgétaire (SG/DAF) ·
Responsable des achats · Magasinier · Auditeur (lecture). Chacun ne voit que ce que son rôle et sa structure permettent.

## Installation
```bash
python -m venv venv && source venv/bin/activate     # Windows : venv\Scripts\activate
pip install -r requirements.txt
python manage.py makemigrations comptes gestion
python manage.py migrate
python manage.py seed_demo       # données fictives (développement uniquement)
python manage.py runserver
```
Comptes de démo : `admin`, `sg`, `resp_dsi`, `agent_dsi`, `resp_lois`, `agent_lois`, `acheteur`, `magasinier`, `auditeur`
(mot de passe `Ang@Demo2026!` – à changer). Prix, budgets et fournisseurs de démonstration sont fictifs.

## Scénario de test rapide
`agent_dsi` crée un besoin → le soumet → `resp_dsi` valide → `sg` arbitre → `magasinier` sert (bon de sortie)
→ `rapport_consommation` et `planification` se mettent à jour. Pour un équipement : `acheteur` commande,
`magasinier` réceptionne (inventaire créé), puis une demande l'affecte automatiquement.

## Logo
`static/img/logo_ang.svg` est un logo **provisoire**. Placez le logo officiel dans `static/img/` et modifiez `APP_LOGO`
dans `besoins_gn/settings.py`.

## Production
`DJANGO_DEBUG=0`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, PostgreSQL, Gunicorn + Nginx, HTTPS, sauvegardes.
Bootstrap est chargé par CDN (à héberger localement sur un réseau sans Internet).
