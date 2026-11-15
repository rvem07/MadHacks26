export type Status='pending'|'approved'|'denied'|'cancelled';
export type Kind='PTO'|'sick';
export interface Request {id:string;employee_email:string;start_date:string;end_date:string;type:Kind;status:Status;approver_email:string;created_at:string}
export interface Employee {email:string;name:string;team:string;manager:string;role:'employee'|'manager'|'ceo'}
