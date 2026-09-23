migrate((app) => {
  const agents = app.findCollectionByNameOrId("agents");
  agents.fields.add(new BoolField({name: "disabled"}));
  agents.authToken.duration = 604800;
  agents.authRule = "disabled = false";
  agents.createRule = "@request.context = 'oauth2'";
  // Account updates remain operator-only, including the disabled flag.
  agents.updateRule = null;
  app.save(agents);

  const employees = app.findCollectionByNameOrId("employees");
  employees.fields.add(new EmailField({name: "work_email", hidden: true}));
  employees.indexes.push("CREATE UNIQUE INDEX idx_employees_work_email ON employees (work_email COLLATE NOCASE) WHERE work_email != ''");
  employees.createRule += " && @request.body.work_email:isset = false";
  employees.updateRule += " && @request.body.work_email:isset = false";
  app.save(employees);
  require(`${__hooks}/deploy.js`).googleOAuth(app);
}, () => {
  throw new Error("Identity and account access rollback requires a deliberate backup restore.");
});
