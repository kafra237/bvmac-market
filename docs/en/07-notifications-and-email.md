# Notifications and email

The platform has several delivery paths: web push, weekly email, market alerts and administrator campaigns.

A successful import can trigger one “new market data” push when the stored market date advances. It does not notify again for a re-import of the same market date. Weekly email is a generated market report with tables and PNG charts embedded in the email itself.

From `/stat`, an administrator can target one or several registered accounts with a free-form email, the weekly report or a push notification. Delivery attempts are recorded per campaign and per recipient; ineligible accounts are skipped rather than causing the whole campaign to fail.
