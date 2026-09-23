// Only the validated server-side Google hook can set this request-local value.
// Password login and client-supplied email/verified fields cannot invoke linking.
function link(e) {
  const email = e.get("peoplecontext.google.email");
  if (!email || e.authMethod !== "oauth2") return e.next();
  e.app.runInTransaction((app) => {
    const account = app.findRecordById("agents", e.record.id);
    if (account.getBool("disabled") || account.getString("email").toLowerCase() !== email) {
      throw new ForbiddenError("The account identity changed; contact an operator.");
    }
    const employees = app.findRecordsByFilter("employees", "work_email:lower = {:email}", "", 2, 0, {email});
    if (employees.length > 1) throw new ForbiddenError("Employee email is ambiguous; contact an operator.");
    const links = app.findRecordsByFilter("account_links", "account = {:account}", "", 1, 0, {account: account.id});
    if (links.length) {
      // Existing explicit authority is never replaced or silently moved.
      if (employees.length && links[0].getString("employee") !== employees[0].id) {
        throw new ForbiddenError("The employee link conflicts with the verified email; contact an operator.");
      }
      return;
    }
    if (!employees.length) return;
    const employee = employees[0];
    const owners = app.findRecordsByFilter("account_links", "employee = {:employee}", "", 1, 0, {employee: employee.id});
    if (owners.length) throw new ForbiddenError("This employee is already linked to another account; contact an operator.");
    const record = new Record(app.findCollectionByNameOrId("account_links"));
    record.set("account", account.id);
    record.set("employee", employee.id);
    app.save(record);
  });
  return e.next();
}

function update(e) {
  if (e.record.getString("work_email") === e.record.original().getString("work_email")) return e.next();
  const originalApp = e.app;
  originalApp.runInTransaction((app) => {
    e.app = app;
    try {
      const links = app.findRecordsByFilter("account_links", "employee = {:employee}", "", 1, 0, {employee: e.record.id});
      if (links.length) throw new BadRequestError("Remove the employee account link before changing its work email.");
      e.next();
    } finally { e.app = originalApp; }
  });
}

module.exports = {link, update};
