FROM node:20-alpine AS build

WORKDIR /app

# Copy package files first for better layer caching
COPY package.json yarn.lock* package-lock.json* ./

# Use yarn so the "resolutions" field in package.json is respected
# (pins core-js-compat to avoid babel-preset-react-app build errors)
RUN yarn install --network-timeout 300000

RUN echo "cache bust 14"
COPY . .
RUN yarn build

FROM nginx:alpine
COPY --from=build /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
