import { api } from './client';
import type { AuditLog, PriceHistory, Vehicle, VehicleListParams, VehiclePage, VehicleStatusCounts } from '../types';

export async function listVehicles(params?: VehicleListParams): Promise<VehiclePage> {
  const { data } = await api.get<VehiclePage>('/api/vehicles', { params });
  return data;
}

/** Vehicles per status (active / sold / removed), for one dealer or all of them. */
export async function getVehicleStatusCounts(dealerId?: number): Promise<VehicleStatusCounts> {
  const { data } = await api.get<VehicleStatusCounts>('/api/vehicles/status-counts', {
    params: dealerId === undefined ? undefined : { dealer_id: dealerId },
  });
  return data;
}

export async function getVehicle(id: number): Promise<Vehicle> {
  const { data } = await api.get<Vehicle>(`/api/vehicles/${id}`);
  return data;
}

export async function getPriceHistory(id: number): Promise<PriceHistory[]> {
  const { data } = await api.get<PriceHistory[]>(`/api/vehicles/${id}/price-history`);
  return data;
}

export async function getAuditLog(id: number): Promise<AuditLog[]> {
  const { data } = await api.get<AuditLog[]>(`/api/vehicles/${id}/audit-log`);
  return data;
}
