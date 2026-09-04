declare module "node:sqlite" {
  export class StatementSync {
    all(...params: any[]): any[];
    get(...params: any[]): any;
    run(...params: any[]): { changes: number; lastInsertRowid: number | bigint };
  }

  export class DatabaseSync {
    constructor(
      location: string,
      options?: {
        open?: boolean;
        readOnly?: boolean;
        enableForeignKeyConstraints?: boolean;
      }
    );
    close(): void;
    exec(sql: string): void;
    prepare(sql: string): StatementSync;
  }
}
