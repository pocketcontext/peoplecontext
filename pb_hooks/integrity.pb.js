/// <reference path="../pb_data/types.d.ts" />
// Execute hooks also cover validated internal saves and batch writes. The writer transaction
// serializes graph inspection and mutation; a request-only validation could race another writer.
onRecordCreateExecute((e) => require(`${__hooks}/integrity.js`).reportingLine(e), "reporting_lines");
onRecordUpdateExecute((e) => require(`${__hooks}/integrity.js`).reportingLine(e), "reporting_lines");
