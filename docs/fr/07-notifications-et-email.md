# Notifications et email

La plateforme utilise le web push, les alertes, la synthèse hebdomadaire et les campagnes administrateur.

Après un import, une notification « nouvelles données » n’est envoyée que si la date de marché enregistrée a réellement avancé. Un retraitement identique ne renvoie donc pas le même push.

La synthèse hebdomadaire est un vrai rapport HTML avec tableaux et graphiques PNG intégrés au message. Depuis `/stat`, l’administrateur peut sélectionner un ou plusieurs comptes puis envoyer un email libre, cette synthèse ou un push ciblé. Les échecs et comptes non éligibles sont tracés par destinataire.
