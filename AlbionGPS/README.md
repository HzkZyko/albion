# Albion GPS

Une petite application Windows pour calculer des itinéraires entre zones d'Albion Online, avec option **Routes sûres uniquement** (Blue + Yellow) ou **Toutes zones** (Blue + Yellow + Red + Black).

Objectif à terme : overlay transparent par-dessus le jeu qui indique en temps réel où aller, avec détection automatique de la zone via packet sniffing et cercle sur la sortie à prendre via OpenCV. On avance par phases.

---

## 1. Installer Python (une seule fois)

1. Va sur https://www.python.org/downloads/windows/
2. Télécharge la dernière version stable (Python 3.12.x ou plus)
3. **TRÈS IMPORTANT** : à l'installation, coche la case **"Add python.exe to PATH"** en bas de la première fenêtre, puis clique sur "Install Now"
4. Vérification : ouvre une invite de commandes (`Windows` → tape `cmd`) et tape `python --version`. Tu dois voir `Python 3.12.x`.

## 2. Installer Npcap (pour la détection auto de zone)

La détection automatique de la zone courante fonctionne en écoutant les paquets réseau du jeu (protocole Photon, UDP 5056). C'est une méthode **passive** : on ne touche jamais au processus Albion, on lit juste ce qui passe sur ta carte réseau. C'est ce que fait [Albion Online Data Project](https://www.albion-online-data.com/) depuis des années.

1. Télécharge Npcap : https://npcap.com
2. Lance l'installeur et **coche** la case **"Install Npcap in WinPcap API-compatible Mode"**
3. Laisse les autres options par défaut

Si tu ne veux pas de détection auto, tu peux sauter cette étape : l'app se rabattra sur la sélection manuelle (cliquer sur une ligne du trajet).

## 3. Lancer l'app

- **Double-clic sur `run.bat`** (le plus simple)

Au premier lancement, `run.bat` crée automatiquement un environnement virtuel Python dans `.venv` et installe toutes les dépendances (PyQt6, psutil, scapy). Il demande ensuite une élévation en administrateur — c'est **obligatoire** pour que scapy puisse capturer les paquets réseau. Accepte l'invite UAC.

Les lancements suivants sont instantanés.

## 4. Utilisation

### Calculer un itinéraire

1. Choisis une zone de **Départ** et d'**Arrivée**
2. Coche/décoche **"Routes sûres uniquement"** selon ton besoin
3. Clique sur **Calculer l'itinéraire**

La liste des zones à traverser apparaît, code couleur par zone.

### Overlay in-game

1. Lance **Albion Online** normalement
2. Dans Albion GPS, clique sur **"Activer overlay in-game"**
3. L'app détecte la fenêtre d'Albion et affiche un HUD semi-transparent en haut à droite, qui suit le jeu automatiquement quand tu déplaces la fenêtre
4. Si la case **"Detection auto de la zone"** est cochée (par défaut), le sniffer Photon démarre en parallèle et met à jour ta zone courante en temps réel dès que tu traverses un portail — aucune action manuelle requise
5. En fallback, tu peux toujours **cliquer sur une ligne du trajet** pour corriger la zone si jamais la détection auto se trompe
6. L'overlay affiche la prochaine zone à rejoindre, le cap cardinal réel (N/NE/E/SE/S/SW/W/NW calculé depuis les coordonnées de la carte du monde), le nombre de sauts restants et une flèche directionnelle

> ⚠️ L'overlay ne capte pas les clics (`WindowTransparentForInput`), donc tu peux jouer normalement au travers. Il ne lit pas la mémoire du jeu : il se contente d'écouter passivement les paquets réseau sortant de ta propre machine. **Passif, non intrusif, conforme aux ToS** (même méthode que Albion Online Data Project).

### Calibration du sniffer Photon (si la détection auto ne marche pas)

Si au bout de quelques changements de zone le statut reste `en attente d'un paquet Photon...`, c'est que la v1 du sniffer (heuristique par scan de strings) ne trouve pas le bon pattern. Dans ce cas on peut capturer des paquets bruts pour analyser et améliorer la détection :

1. Lance Albion GPS au moins une fois pour que `.venv` soit créé
2. Ouvre un terminal en admin dans `AlbionGPS`
3. Lance `.venv\Scripts\python.exe tools\capture_photon.py`
4. Pendant que ça tourne, va dans Albion et change de zone 4-5 fois en notant les noms
5. Ctrl+C pour arrêter la capture
6. Envoie-moi le fichier `data/photon_capture.log` et la liste des zones traversées — je pourrai identifier l'event exact à écouter et basculer le sniffer sur un décodage propre.

---

## Structure du projet

```
AlbionGPS/
├── data/
│   └── zones.json        ← Base de données des zones (éditable)
├── src/
│   ├── __init__.py
│   ├── main.py           ← Interface PyQt6 + logique app
│   ├── pathfinding.py    ← Moteur Dijkstra
│   ├── game_detector.py  ← Détection fenêtre Albion (Win32)
│   └── overlay.py        ← Overlay transparent always-on-top
├── tools/                ← Scripts utilitaires (importeur de zones à venir)
├── requirements.txt
├── run.bat
└── README.md
```

## État des phases

- [x] **Phase 1** — Moteur GPS + UI basique
- [x] **Phase 2a** — Détection fenêtre Albion + overlay transparent
- [x] **Phase 2b** — Import des vraies zones d'Albion (414 zones, 636 connexions) + calcul de cap cardinal réel entre zones
- [ ] **Phase 3a** — Packet sniffer (Albion Data Project) pour détecter automatiquement la zone et la position du joueur
- [ ] **Phase 3b** — Template matching OpenCV pour encercler l'icône de sortie sur la minimap
- [ ] **Phase 4** — Autres outils à décider ensemble

## Importer / mettre à jour la base de zones

Le fichier `data/zones.json` est déjà généré (414 zones du monde principal d'Albion). Pour le régénérer après une mise à jour du jeu :

1. Télécharge `zoneData.json` depuis https://raw.githubusercontent.com/SugarF0x/albion-navigator/main/Resources/zoneData.json
2. Place-le dans `data/zoneData_raw.json`
3. Lance :
   ```
   python tools/convert_zonedata.py
   ```
4. Le script écrit un nouveau `data/zones.json` avec les 414 zones, leurs positions et leurs connexions. Le mapping de couleurs est :
   - types 0, 1 → **blue** (Crosses, villes, rests, portals)
   - type 2 → **yellow**
   - types 3, 4 → **red**
   - type 5 → **black** (Outlands)
   - type 6 → ignoré (Avalonian Roads dynamiques)
   - Caerleon est forcé en **red** car la structure du dataset la marque comme "city"

## Étendre la base de zones manuellement

Le fichier `data/zones.json` est simple à éditer :

```json
{
  "zones": {
    "NomDeLaZone": {"color": "blue|yellow|red|black", "tier": 4, "biome": "forest"}
  },
  "connections": [
    ["ZoneA", "ZoneB"]
  ]
}
```

1. Trouve le nom exact d'une zone sur https://wiki.albiononline.com
2. Ajoute-la dans `zones`
3. Ajoute ses connexions (les portails visibles sur la minimap)
4. Relance l'app
