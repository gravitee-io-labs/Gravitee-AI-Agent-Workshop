FROM node:25.9-alpine
WORKDIR /app
COPY package.json ./
RUN npm install --production
COPY . .
EXPOSE 9001 9002 4318
CMD ["node", "server.js"]
