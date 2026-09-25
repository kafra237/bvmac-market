# Web application

The `web/` folder contains the browser interface, not server-side business logic. Authenticated features live behind the canonical `/app?view=...` route. Public login, information and demo pages remain separate by nature.

The interface is responsive from narrow phones to desktop screens. The navigation is route-based, so adding a page does not shift labels onto the wrong destinations. `i18n.js` provides French/English translation, including content added dynamically after page load.

The project is also installable as a PWA. `pwa.js`, `sw.js` and `/version.json` work together to detect a newly deployed release. A deployed release receives a unique runtime token, so open browser tabs and installed PWAs can refresh assets without asking users to clear their cache manually. Private API responses are not cached.
