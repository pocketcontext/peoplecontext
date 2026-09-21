// This is trusted application policy code, never loaded from personnel records.
function reportingLine(e) {
  const app = e.app;
  app.runInTransaction((txApp) => {
    e.app = txApp;
    try {
      const employee = e.record.getString("employee");
      let manager = e.record.getString("manager");
      const visited = new Set([employee]);
      while (manager) {
        if (visited.has(manager)) {
          throw new BadRequestError("Reporting relationships must not contain a cycle.", {
            manager: new ValidationError("validation_reporting_cycle", "Self-management and reporting cycles are forbidden."),
          });
        }
        visited.add(manager);
        // Exclude the old edge on updates, including when its employee changes.
        const edges = txApp.findRecordsByFilter("reporting_lines", "employee = {:employee} && id != {:id}", "", 1, 0,
          {employee: manager, id: e.record.id});
        manager = edges.length ? edges[0].getString("manager") : "";
      }
      e.next();
    } finally {
      e.app = app;
    }
  });
}

module.exports = {reportingLine};
