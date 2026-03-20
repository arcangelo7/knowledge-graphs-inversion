// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import rehypeExternalLinks from 'rehype-external-links';

export default defineConfig({
	site: 'https://arcangelo7.github.io',
	base: '/knowledge-graphs-inversion',
	markdown: {
		rehypePlugins: [
			[rehypeExternalLinks, { target: '_blank', rel: ['noopener', 'noreferrer'] }],
		],
	},
	integrations: [
		starlight({
			title: 'RML Inversion',
			social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/arcangelo7/knowledge-graphs-inversion' }],
			sidebar: [
				{ label: 'Overview', slug: 'index' },
				{
					label: 'Getting started',
					items: [
						{ label: 'Installation', slug: 'getting-started/installation' },
						{ label: 'Usage', slug: 'getting-started/usage' },
					],
				},
				{
					label: 'Concepts',
					items: [
						{ label: 'How inversion works', slug: 'concepts/how-it-works' },
						{ label: 'Limitations', slug: 'concepts/limitations' },
					],
				},
				{
					label: 'Evaluation',
					items: [
						{ label: 'Conformance tests', slug: 'evaluation/conformance-tests' },
						{ label: 'Benchmarking', slug: 'evaluation/benchmarking' },
					],
				},
			],
		}),
	],
});
