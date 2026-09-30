export interface Entity {
  table: string;
  label: string;
  title: string[];
  subtitle?: string[];
  search: string[];
  filters?: Record<string, string[]>;
  relations?: Record<string, string>;
  hidden?: string[];
  markdown?: string[];
  menu?: boolean;
  files?: string[];
  relationLabels?: Record<string, string>;
}
export const app: { name: string; authCollection: string; entities: Entity[] } =
  {
    name: "PeopleContext",
    authCollection: "agents",
    entities: [
      {
        table: "employees",
        label: "People",
        title: ["name"],
        search: ["id", "name", "job_title", "department"],
        subtitle: ["job_title", "department"],
      },
      {
        table: "compensation",
        label: "Compensation",
        title: ["currency"],
        search: ["id", "currency", "effective_date"],
        relations: {
          employee: "employees",
        },
      },
      {
        table: "personal_details",
        label: "Personal details",
        title: ["id"],
        search: ["id", "home_address", "emergency_contact"],
        relations: {
          employee: "employees",
        },
      },
      {
        table: "hr_notes",
        label: "HR notes",
        title: ["body"],
        search: ["id", "body"],
        relations: {
          employee: "employees",
        },
        markdown: ["body"],
      },
    ],
  };
