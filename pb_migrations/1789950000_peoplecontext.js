/// <reference path="../pb_data/types.d.ts" />
migrate((app) => {
  const agentAccess = "@request.auth.id != '' && @request.auth.collectionName = 'agents'";
  const hrAccess = agentAccess + " && @collection.hr_members.account ?= @request.auth.id";
  app.save(new Collection({
    type: "auth", name: "agents", listRule: null, viewRule: null,
    createRule: null, updateRule: null, deleteRule: null,
    fields: [{name: "name", type: "text", required: true, max: 200}],
    passwordAuth: {enabled: true, identityFields: ["email"]},
    authToken: {duration: 86400}, authAlert: {enabled: false},
  }));
  const text = (name, required = false, max = 500) => ({name, type: "text", required, max});
  const relation = (name, collection) => ({
    name, type: "relation", collectionId: app.findCollectionByNameOrId(collection).id,
    maxSelect: 1, required: true, cascadeDelete: false,
  });
  function create(name, fields, indexes = []) {
    app.save(new Collection({type: "base", name,
      listRule: null, viewRule: null, createRule: null, updateRule: null, deleteRule: null,
      fields: fields.concat([
        {name: "created", type: "autodate", onCreate: true, onUpdate: false},
        {name: "updated", type: "autodate", onCreate: true, onUpdate: true},
      ]), indexes}));
  }
  create("employees", [text("name", true, 200), text("job_title"), text("department")]);
  create("account_links", [relation("account", "agents"), relation("employee", "employees")], [
    "CREATE UNIQUE INDEX idx_account_links_account ON account_links (account)",
    "CREATE UNIQUE INDEX idx_account_links_employee ON account_links (employee)",
  ]);
  create("hr_members", [relation("account", "agents")], [
    "CREATE UNIQUE INDEX idx_hr_members_account ON hr_members (account)",
  ]);
  create("reporting_lines", [relation("employee", "employees"), relation("manager", "employees")], [
    "CREATE UNIQUE INDEX idx_reporting_lines_employee ON reporting_lines (employee)",
    "CREATE INDEX idx_reporting_lines_manager ON reporting_lines (manager)",
  ]);
  create("compensation", [relation("employee", "employees"),
    {name: "annual_salary_minor", type: "number", min: 0, max: 9007199254740991, onlyInt: true},
    {name: "currency", type: "text", required: true, min: 3, max: 3, pattern: "^[A-Z]{3}$"},
    {name: "effective_date", type: "date", required: true},
  ], ["CREATE UNIQUE INDEX idx_compensation_employee ON compensation (employee)"]);
  create("personal_details", [relation("employee", "employees"), text("home_address", false, 4000),
    text("emergency_contact", false, 4000)], [
    "CREATE UNIQUE INDEX idx_personal_details_employee ON personal_details (employee)",
  ]);
  create("hr_notes", [relation("employee", "employees"), text("body", true, 20000)], [
    "CREATE INDEX idx_hr_notes_employee ON hr_notes (employee)",
  ]);
  // Enable writes only after the authority collection exists and its rules can be resolved.
  for (const name of ["employees", "compensation", "personal_details", "hr_notes"]) {
    const collection = app.findCollectionByNameOrId(name);
    collection.createRule = hrAccess;
    collection.updateRule = hrAccess;
    collection.deleteRule = hrAccess;
    app.save(collection);
  }
  const settings = app.settings();
  settings.batch.enabled = true;
  settings.batch.maxRequests = 20;
  settings.batch.timeout = 5;
  app.save(settings);
}, (app) => {
  for (const name of ["hr_notes", "personal_details", "compensation", "reporting_lines", "hr_members",
    "account_links", "employees", "agents"]) {
    app.delete(app.findCollectionByNameOrId(name));
  }
  const settings = app.settings();
  settings.batch.enabled = false;
  settings.batch.maxRequests = 50;
  settings.batch.timeout = 3;
  app.save(settings);
});
