// Identity claims come from PocketBase's server-to-server Google userinfo request.
// Neither createData nor the authorization URL's hd hint is trusted.
function domain() {
  const value = $os.getenv("PEOPLECONTEXT_GOOGLE_WORKSPACE_DOMAIN");
  if (!value) return "";
  if (value.length > 253 || value !== value.toLowerCase() ||
      !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/.test(value)) {
    throw new Error("PEOPLECONTEXT_GOOGLE_WORKSPACE_DOMAIN must be a lowercase DNS domain");
  }
  return value;
}

function authenticate(e) {
  const workspace = domain();
  if (e.record && e.record.getBool("disabled")) {
    throw new ForbiddenError("This PeopleContext account is disabled.");
  }
  if (!workspace) {
    if (!e.record) throw new ForbiddenError("PeopleContext account provisioning is disabled.");
    return e.next();
  }

  const user = e.oAuth2User;
  const raw = user && user.rawUser;
  const email = user && user.email;
  if (e.providerName !== "google" || !raw || raw.email_verified !== true ||
      typeof email !== "string" || !email || raw.email !== email ||
      raw.hd !== workspace || email.split("@").length !== 2 ||
      email.split("@")[1].toLowerCase() !== workspace) {
    throw new ForbiddenError("A verified Google Workspace account in the configured domain is required.");
  }
  // PocketBase's initial email lookup is case-sensitive. Resolve case variants
  // before provisioning so an existing identity (including a disabled one)
  // cannot be bypassed by Google's email casing.
  const matches = e.app.findRecordsByFilter("agents", "email:lower = {:email}", "", 2, 0,
    {email: email.toLowerCase()});
  if (matches.length > 1 || (e.record && matches.length && matches[0].id !== e.record.id)) {
    throw new ForbiddenError("Google identity matches conflicting PeopleContext accounts; contact an operator.");
  }
  if (!e.record && matches.length === 1) {
    e.record = matches[0];
    e.isNewRecord = false;
  }
  if (e.record && e.record.getBool("disabled")) {
    throw new ForbiddenError("This PeopleContext account is disabled.");
  }
  // PocketBase can otherwise fall back to the currently authenticated user when
  // linking an OAuth provider. Never attach another person's Google identity.
  if (e.record && e.record.getString("email").toLowerCase() !== email.toLowerCase()) {
    throw new ForbiddenError("Google identity does not match the PeopleContext account.");
  }
  // Keep the stored spelling while allowing PocketBase's exact comparison to
  // verify the existing account using the validated, equivalent Google email.
  if (e.record) user.email = e.record.getString("email");

  // Replace the entire payload: no client-selected id, password, flags, or
  // identity fields may enter a JIT account. PocketBase generates its password
  // and verifies the email after the ordinary transactional creation hooks.
  const name = typeof user.name === "string" ? user.name.trim().slice(0, 200) : "";
  e.createData = {email: email, name: name || email.split("@")[0].slice(0, 200)};
  e.set("peoplecontext.google.email", email.toLowerCase());
  return e.next();
}

module.exports = {domain, authenticate};
