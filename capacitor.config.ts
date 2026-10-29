import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.beamind.smartraz',
  appName: 'RAZ 小小精读',
  webDir: 'dist',
  plugins: { SystemBars: { style: 'LIGHT' } },
  android: { backgroundColor: '#f7f6f0', allowMixedContent: false, webContentsDebuggingEnabled: false },
};
export default config;
