import type { FullConfig } from '@nekosu/maa-tools'

const config: FullConfig = {
  cwd: import.meta.dirname,
  // 固定已使用的框架版本，避免 latest 漂移到预发布版或平台包发布不同步。
  maaVersion: '5.14.3',
  interfacePath: 'assets/interface.json',
  check: {}
}

export default config
