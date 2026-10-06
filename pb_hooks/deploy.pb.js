routerAdd("GET", "/up", (e) => require(`${__hooks}/deploy.js`).up(e), $apis.skipSuccessActivityLog());
onBootstrap((e) => {
  e.next();
  // Frozen startup preserves existing settings and authentication identities.
  if (e.app.store().get("pocketcontextMaintenanceReadOnly") === true) {
    require(`${__hooks}/storage.js`).configure(e.app, true);
    return;
  }
  const deploy = require(`${__hooks}/deploy.js`);
  deploy.settings(e.app);
  deploy.googleOAuth(e.app);
});
