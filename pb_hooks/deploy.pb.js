routerAdd("GET", "/up", (e) => require(`${__hooks}/deploy.js`).up(e), $apis.skipSuccessActivityLog());
onBootstrap((e) => {
  e.next();
  const deploy = require(`${__hooks}/deploy.js`);
  deploy.settings(e.app);
  deploy.googleOAuth(e.app);
});
