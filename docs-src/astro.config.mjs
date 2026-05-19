import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
  site: 'https://elara-labs.github.io',
  base: '/code-context-engine/guide',
  outDir: '../docs/guide',
  integrations: [
    starlight({
      title: 'Code Context Engine',
      logo: { src: './src/assets/logo.svg' },
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/elara-labs/code-context-engine' },
      ],
      sidebar: [
        { slug: 'introduction' },
        { slug: 'why-cce' },
        { slug: 'getting-started' },
        {
          label: 'Agent Setup',
          items: [
            { slug: 'agents/overview' },
            { slug: 'agents/claude' },
            { slug: 'agents/cursor' },
            { slug: 'agents/copilot' },
            { slug: 'agents/gemini' },
            { slug: 'agents/codex' },
            { slug: 'agents/opencode' },
            { slug: 'agents/tabnine' },
          ],
        },
        { slug: 'configuration' },
        { slug: 'cli-reference' },
        { slug: 'how-it-works' },
        { slug: 'savings-tracking' },
        { slug: 'faq' },
      ],
    }),
  ],
});
