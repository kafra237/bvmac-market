#!/usr/bin/env python3
"""À exécuter via sudo sur le serveur ; aucun code transmis par email."""
import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess

def main():
    p=argparse.ArgumentParser();p.add_argument('--challenge',required=True);p.add_argument('--database',default='bvmac');a=p.parse_args()
    if os.geteuid()!=0:p.error('Exécute cette commande avec sudo sur le serveur.')
    if not re.fullmatch('[a-f0-9]{48}',a.challenge) or not re.fullmatch('[a-zA-Z_][a-zA-Z0-9_]{0,62}',a.database):p.error('Paramètre invalide')
    # Identifiers above are strictly validated, not user-provided SQL.
    query=f"SELECT admin_name FROM admin.console_challenge WHERE ticket='{a.challenge}' AND expires_at>now() AND code_hash IS NULL;"
    cmd=['runuser','-u','postgres','--','psql','-X','-v','ON_ERROR_STOP=1','-At','-d',a.database]
    r=subprocess.run(cmd,input=query,text=True,capture_output=True,check=True)
    name=r.stdout.strip()
    if not name:raise SystemExit('Demande introuvable, expirée ou code déjà généré.')
    print('Demande d’accès administrateur :',json.dumps(name,ensure_ascii=True))
    if input('Confirmer cette demande initiée dans ton onglet ? [oui/non] ').strip().lower()!='oui':raise SystemExit('Annulé')
    code=f'{secrets.randbelow(100000000):08d}'
    digest=hashlib.sha256((a.challenge+'|'+code).encode()).hexdigest()
    query=f"UPDATE admin.console_challenge SET code_hash='{digest}' WHERE ticket='{a.challenge}' AND expires_at>now() AND code_hash IS NULL RETURNING ticket;"
    r=subprocess.run(cmd,input=query,text=True,capture_output=True,check=True)
    if a.challenge not in r.stdout:raise SystemExit('Demande déjà traitée ou expirée.')
    print('Code à saisir dans ton onglet :',code)

if __name__=='__main__':main()
