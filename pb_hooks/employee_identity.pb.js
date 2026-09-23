onRecordAuthRequest((e) => require(`${__hooks}/employee_identity.js`).link(e), "agents");
onRecordUpdateExecute((e) => require(`${__hooks}/employee_identity.js`).update(e), "employees");
