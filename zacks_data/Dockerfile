FROM node:18-slim

WORKDIR /app

# Copy package files
COPY package*.json ./
RUN npm install

# Copy application files
COPY . .

# Expose port 3000
EXPOSE 3000

# Start the application
CMD ["node", "index.js"]
