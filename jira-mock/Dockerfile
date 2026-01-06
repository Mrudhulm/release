FROM node:20-alpine

WORKDIR /app

RUN npm install -g json-server

COPY jira.json /app/jira.json

EXPOSE 3000

CMD ["json-server", "--host", "0.0.0.0", "--port", "3000", "--watch", "jira.json"]
