export const isStaff = (user) => user?.role === 'admin' || user?.role === 'agent';
