FROM node:25-alpine
WORKDIR /app
COPY package.json ./
RUN npm install --production
COPY . .
EXPOSE 9001 9002
CMD ["node", "server.js"]
