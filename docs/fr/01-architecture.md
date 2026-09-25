# Architecture

```text
Site public BVMAC
   │
   ├─ Bulletins PDF ──> pipeline/ ──> bvmac_master.xlsx
   │                                      │
   │                                      v
   │                                  database/
   │                                      │
   │                                      v
   └──────────────────────────────> PostgreSQL
                                          │
                    ┌─────────────────────┼─────────────────────┐
                    v                     v                     v
              backend/api/          backend/jobs/             ml/
                    │                     │                     │
                    └──────────────┬──────┴─────────────┬──────┘
                                   v                    v
                                  Nginx <────────────── web/
                                   │
                                   v
                              Navigateur / PWA
```

L’ETL est responsable des règles d’extraction. L’import PostgreSQL stocke ce que dit le classeur, sans refaire silencieusement les corrections métier. Cette séparation permet de savoir si une anomalie vient du document source, de l’extraction, de l’import ou de l’affichage.
