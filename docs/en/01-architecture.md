# Architecture

```text
Public BVMAC website
   │
   ├─ PDF bulletins ──> pipeline/ ──> bvmac_master.xlsx
   │                                      │
   │                                      v
   │                                 database/
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
                              Browser / PWA
```

The ETL owns extraction rules. The PostgreSQL importer stores what the workbook says rather than silently “fixing” it a second time. This separation is deliberate: when a number is wrong, contributors can identify whether the source document, extraction, import or presentation layer is responsible.

Production jobs are isolated through systemd services and timers. Nginx terminates TLS and proxies only to a FastAPI process bound to localhost.
