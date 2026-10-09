export type DeviceMode = 'desktop' | 'mobile';

export function resolveDeviceMode(userAgent: string, uaDataMobile?: boolean): DeviceMode {
  // Tablets keep the existing desktop UI. A narrow PC window and touch support
  // alone must never opt a PC into the mobile UI.
  if (/iPad/i.test(userAgent)) return 'desktop';
  if (typeof uaDataMobile === 'boolean') return uaDataMobile ? 'mobile' : 'desktop';
  return /Mobi|iPhone|iPod/i.test(userAgent) ? 'mobile' : 'desktop';
}

export function getDeviceMode(): DeviceMode {
  const client = navigator as Navigator & { userAgentData?: { mobile?: boolean } };
  return resolveDeviceMode(client.userAgent, client.userAgentData?.mobile);
}
