import { type Config } from "prettier";

const config: Config = {
  plugins: ["prettier-plugin-jinja-template", "prettier-plugin-tailwindcss"],

  overrides: [
    {
      files: ["app/templates/**/*.html", "**/*.jinja", "**/*.jinja2"],
      options: {
        parser: "jinja-template",
      },
    },
  ],
};

export default config;
