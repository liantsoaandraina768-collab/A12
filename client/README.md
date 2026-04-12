# Client Setup

1. Ensure you have `libimobiledevice` and `pymobiledevice3` installed.
2. Install `PyQt5` for the GUI interface: `pip install PyQt5`.
3. Open `activator.py` in a text editor.
4. Locate line 20: `self.api_url = "https://your-domain.com/index.php"`
5. Change `your-domain.com` to the actual URL where you deployed the server folder.
6. Run: `python3 activator.py`

## Interface Mobidoc A5-like

- Le client utilise maintenant une interface PyQt5 similaire à `mobidoc a5`.
- L'application vérifie le numéro de série auprès du serveur avant d'activer.
- Si le SN n'est pas enregistré, un message s'affiche avec un lien d'achat.
- Pour lancer en mode terminal: `python3 activator.py --cli`

## Requirements

- Install dependencies with: `pip install -r ../requirements.txt`
- If PyQt5 n'est pas installé, le GUI ne pourra pas démarrer.
