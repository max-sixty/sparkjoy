import { expect, test } from 'claude-code/testing'
import { installOf } from '../hooks/update'

// The plugin under test runs from its folder rather than from an install, so the
// check stops at its first step in every test: this test pins that step.
test('an install is read from the path Claude Code gives it, on Windows too', () => {
  expect(
    installOf('/home/u/.claude/plugins/cache/sparkjoy/sparkjoy/55178b59fec8'),
  ).toEqual({
    plugins: '/home/u/.claude/plugins',
    marketplace: 'sparkjoy',
    commit: '55178b59fec8',
  })
  expect(
    installOf('C:\\Users\\u\\.claude\\plugins\\cache\\mine\\sparkjoy\\55178b59fec8'),
  ).toEqual({
    plugins: 'C:\\Users\\u\\.claude\\plugins',
    marketplace: 'mine',
    commit: '55178b59fec8',
  })
  // A folder loaded with --plugin-dir, and an install whose manifest sets a version.
  expect(installOf('/home/u/workspace/sparkjoy')).toBeNull()
  expect(installOf('/home/u/.claude/plugins/cache/sparkjoy/sparkjoy/1.2.0')).toBeNull()
})
